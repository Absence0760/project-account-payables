"""S3 Object-Lock audit-shipping adapter.

Each `ship(rows)` call writes a single gzip-compressed JSONL file to S3
under `audit/<tenant>/<YYYY>/<MM>/<DD>/<timestamp>-<uuid>.jsonl.gz`. The
bucket is expected to have Object Lock enabled in Governance mode with a
default retention period configured by infra (see docs) — the adapter
does NOT configure Object Lock itself. We `head_bucket` on construction
so a misconfigured bucket fails the startup self-test loudly.

One object per batch is intentional: it keeps the ship atomic (either
the PUT succeeded or it didn't), preserves the natural batch boundary
for auditor replay, and avoids S3's 5GB single-PUT limit — even 500
rows of audit JSON compressed well under 1MB in practice.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import uuid
from datetime import UTC, datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.services.audit_shipping.base import AuditLogRow, AuditShippingAdapter
from app.services.audit_shipping.dispatcher import register_audit_shipping_adapter

logger = logging.getLogger(__name__)


@register_audit_shipping_adapter("s3_objectlock")
class S3ObjectLockAdapter(AuditShippingAdapter):
    """Ships audit rows as gzipped JSONL into an Object-Lock-enabled bucket.

    Config:
        bucket_name:  Override AP_AUDIT_SHIPPING_S3_BUCKET.
        region_name:  AWS region (default us-east-1).
        key_prefix:   Optional prefix under which all objects are written
                      (default: "audit").
    """

    provider_name = "s3_objectlock"

    def __init__(self, config: dict):
        super().__init__(config)
        self.bucket = config.get("bucket_name") or settings.audit_shipping_s3_bucket
        if not self.bucket:
            raise ValueError(
                "S3ObjectLockAdapter requires AP_AUDIT_SHIPPING_S3_BUCKET or bucket_name in config."
            )
        region = config.get("region_name") or "us-east-1"
        self.key_prefix = config.get("key_prefix", "audit").strip("/")
        self._client = boto3.client("s3", region_name=region)

    # -- private helpers -----------------------------------------------------

    def _make_key(self, rows: list[AuditLogRow]) -> str:
        # Partition by the FIRST row's tenant + UTC date. Batches coming out
        # of the shipper are single-tenant + sorted by created_at, so this
        # gives a stable, sort-friendly key layout.
        first = rows[0]
        day = first.created_at.astimezone(UTC)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return (
            f"{self.key_prefix}/{first.tenant_db}/"
            f"{day.year:04d}/{day.month:02d}/{day.day:02d}/"
            f"{stamp}-{uuid.uuid4().hex[:8]}.jsonl.gz"
        )

    @staticmethod
    def _encode(rows: list[AuditLogRow]) -> bytes:
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            for row in rows:
                gz.write((json.dumps(row.to_json()) + "\n").encode("utf-8"))
        return buf.getvalue()

    # -- adapter API ---------------------------------------------------------

    async def ship(self, rows: list[AuditLogRow]) -> None:
        if not rows:
            return

        key = self._make_key(rows)
        body = self._encode(rows)

        def _put():
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType="application/x-ndjson",
                ContentEncoding="gzip",
            )

        try:
            await asyncio.to_thread(_put)
            logger.debug(
                "[audit-shipping:s3] wrote %d row(s) to s3://%s/%s",
                len(rows),
                self.bucket,
                key,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "[audit-shipping:s3] put_object failed (%d row(s)): %s",
                len(rows),
                exc,
            )
            raise

    async def test_connection(self) -> bool:
        """Verify the bucket exists and Object Lock is configured on it.

        Object Lock must be configured at bucket-creation time and cannot
        be turned on later, so a missing Object Lock config here is a
        hard failure rather than something the adapter can fix.
        """

        def _check() -> bool:
            self._client.head_bucket(Bucket=self.bucket)
            try:
                resp = self._client.get_object_lock_configuration(Bucket=self.bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code == "ObjectLockConfigurationNotFoundError":
                    logger.error(
                        "[audit-shipping:s3] bucket %s does not have Object "
                        "Lock enabled; refusing to ship.",
                        self.bucket,
                    )
                    return False
                raise
            status = resp.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled")
            return status == "Enabled"

        try:
            return await asyncio.to_thread(_check)
        except (BotoCoreError, ClientError):
            return False
