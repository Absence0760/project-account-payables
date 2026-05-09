"""CloudWatch Logs audit-shipping adapter.

Fans each batch into one `PutLogEvents` call per (tenant, UTC date) log
stream. Log streams are lazily created — CloudWatch returns a specific
`ResourceAlreadyExistsException` that we swallow so repeated ticks don't
bang on `CreateLogStream` with errors.

Why one stream per day per tenant? CloudWatch charges by ingested bytes,
not stream count, and pre-partitioning by tenant + day makes auditor-
driven "show me all events for tenant X on day Y" queries a straight
filter instead of a Log Insights scan.

boto3 is sync; all calls go through `asyncio.to_thread` to stay off the
event loop, matching the SES adapter's pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import UTC

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.services.audit_shipping.base import AuditLogRow, AuditShippingAdapter
from app.services.audit_shipping.dispatcher import register_audit_shipping_adapter

logger = logging.getLogger(__name__)


@register_audit_shipping_adapter("cloudwatch")
class CloudWatchAdapter(AuditShippingAdapter):
    """Ships audit rows to CloudWatch Logs.

    Config:
        log_group_name: Override the default AP_AUDIT_SHIPPING_CLOUDWATCH_GROUP.
        region_name:    Override the default aws region.
    """

    provider_name = "cloudwatch"

    def __init__(self, config: dict):
        super().__init__(config)
        self.log_group = config.get("log_group_name") or settings.audit_shipping_cloudwatch_group
        region = config.get("region_name") or "us-east-1"
        self._client = boto3.client("logs", region_name=region)
        # One-time ensure for the log group; safe to retry.
        self._ensured_group = False
        self._ensured_streams: set[str] = set()

    # -- private helpers -----------------------------------------------------

    def _ensure_log_group(self) -> None:
        if self._ensured_group:
            return
        try:
            self._client.create_log_group(logGroupName=self.log_group)
        except self._client.exceptions.ResourceAlreadyExistsException:
            pass
        self._ensured_group = True

    def _ensure_log_stream(self, stream_name: str) -> None:
        if stream_name in self._ensured_streams:
            return
        try:
            self._client.create_log_stream(logGroupName=self.log_group, logStreamName=stream_name)
        except self._client.exceptions.ResourceAlreadyExistsException:
            pass
        self._ensured_streams.add(stream_name)

    def _put_events(self, stream_name: str, events: list[dict]) -> None:
        # CloudWatch requires events ordered by timestamp ascending.
        events.sort(key=lambda e: e["timestamp"])
        self._client.put_log_events(
            logGroupName=self.log_group,
            logStreamName=stream_name,
            logEvents=events,
        )

    # -- adapter API ---------------------------------------------------------

    async def ship(self, rows: list[AuditLogRow]) -> None:
        if not rows:
            return

        # Bucket rows into (tenant, YYYY-MM-DD) streams.
        buckets: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            day = row.created_at.astimezone(UTC).strftime("%Y-%m-%d")
            stream = f"{row.tenant_db}/{day}"
            buckets[stream].append(
                {
                    "timestamp": int(row.created_at.timestamp() * 1000),
                    "message": json.dumps(row.to_json()),
                }
            )

        def _ship():
            self._ensure_log_group()
            for stream_name, events in buckets.items():
                self._ensure_log_stream(stream_name)
                self._put_events(stream_name, events)

        try:
            await asyncio.to_thread(_ship)
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "[audit-shipping:cloudwatch] put_log_events failed (%d row(s)): %s",
                len(rows),
                exc,
            )
            raise

    async def test_connection(self) -> bool:
        def _check():
            self._client.describe_log_groups(logGroupNamePrefix=self.log_group, limit=1)
            return True

        try:
            return await asyncio.to_thread(_check)
        except (BotoCoreError, ClientError):
            return False
