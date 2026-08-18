"""CloudWatch Logs audit-shipping adapter.

Fans each batch into `PutLogEvents` calls per (tenant, UTC date) log stream.
Log streams are lazily created — CloudWatch returns a specific
`ResourceAlreadyExistsException` that we swallow so repeated ticks don't
bang on `CreateLogStream` with errors.

Why one stream per day per tenant? CloudWatch charges by ingested bytes,
not stream count, and pre-partitioning by tenant + day makes auditor-
driven "show me all events for tenant X on day Y" queries a straight
filter instead of a Log Insights scan.

boto3 is sync; all calls go through `asyncio.to_thread` to stay off the
event loop, matching the SES adapter's pattern.

`PutLogEvents` has hard limits, and this adapter owns them
-----------------------------------------------------------
The API caps a single call at **10 000 events** and **1 MiB** (the sum of the
UTF-8 message bytes plus 26 bytes of framing per event), and a single event at
**256 KiB** on the same accounting. The shipper hands us a whole batch
(`FEOH_AUDIT_SHIPPING_BATCH_SIZE`, default 500 rows) for one tenant, which for
ordinary audit rows lands comfortably inside those caps — but audit `details`
is free-form JSONB, and a handful of fat rows takes the batch past 1 MiB. The
adapter used to POST the batch as ONE call, so AWS answered
`InvalidParameterException`, `ship()` raised, the rows stayed unshipped, and the
NEXT tick re-selected the identical oldest-first batch: a permanent head-of-line
block in which nothing newer for that tenant ever ships again. (This is not
hypothetical — `retention_sweep` carries a comment about the one oversized
`retention.archived` row that jammed the shipper before its details were
trimmed. Trimming that row fixed the symptom; the cap was never respected.)

So the events are chunked here, by both count and bytes, and a single event
that cannot fit even alone has its `details` replaced with a PII-free
truncation marker — the identity of the row (id, org, actor, action, entity,
timestamp) still reaches the tamper-evidence store, the full row stays in the
tenant DB and in the S3 Object Lock copy (which has no such limit), and the
trail keeps moving instead of stopping forever on one row.

A 200 that dropped rows is a failure
------------------------------------
`PutLogEvents` can also return **success** while silently discarding events —
`rejectedLogEventsInfo` names those too old for the log group's retention, or
too far in the future. Stamping `shipped_at` on rows the sink refused is the
exact "green light, no evidence behind it" state the boot-time
`test_connection` probe exists to prevent, so a rejection raises
`AuditShippingRejected` and the rows stay unshipped + visible in
`GET /api/health/sweeps` and the retention manifest.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.services.audit_shipping.base import (
    AuditLogRow,
    AuditShippingAdapter,
    AuditShippingRejected,
)
from app.services.audit_shipping.dispatcher import register_audit_shipping_adapter

logger = logging.getLogger(__name__)

# --- AWS PutLogEvents hard limits (service quotas, not tunables) ------------
#: Max log events in one PutLogEvents call.
MAX_EVENTS_PER_CALL = 10_000
#: Max total size of one call: message UTF-8 bytes + EVENT_OVERHEAD_BYTES each.
MAX_BATCH_BYTES = 1_048_576
#: Per-event framing AWS charges against both the batch and per-event caps.
EVENT_OVERHEAD_BYTES = 26
#: Max size of a single event, on the same message-bytes + overhead accounting.
MAX_EVENT_BYTES = 262_144

#: Marker replacing an oversized row's `details`. Fixed keys, no row content —
#: it is written into the WORM store, so it carries no PII of its own.
TRUNCATION_KEY = "_details_truncated"


def _event_size(event: dict) -> int:
    """Bytes one event costs against the AWS caps. Pure."""
    return len(event["message"].encode("utf-8")) + EVENT_OVERHEAD_BYTES


def _chunk_events(events: list[dict]) -> Iterator[list[dict]]:
    """Split ``events`` into runs that each fit one PutLogEvents call. Pure.

    Order is preserved, so the caller's timestamp sort still holds within and
    across chunks. An event that exceeds the per-call byte budget on its own has
    already been shrunk by :func:`_fit_event`, so it always lands in a chunk.
    """
    chunk: list[dict] = []
    size = 0
    for event in events:
        cost = _event_size(event)
        if chunk and (len(chunk) >= MAX_EVENTS_PER_CALL or size + cost > MAX_BATCH_BYTES):
            yield chunk
            chunk = []
            size = 0
        chunk.append(event)
        size += cost
    if chunk:
        yield chunk


def _fit_event(event: dict, row: AuditLogRow) -> dict:
    """Return ``event`` unchanged, or a details-stripped version if oversized.

    Pure. A single audit row whose JSON exceeds the 256 KiB per-event cap can
    never be ingested as-is; raising instead would block every newer row for
    that tenant forever. Dropping only `details` keeps the row's identity and
    its append-only ordering in the WORM store, and the complete row remains in
    the tenant `audit_log` table and in the S3 Object Lock copy.
    """
    if _event_size(event) <= MAX_EVENT_BYTES:
        return event
    payload = row.to_json()
    original_bytes = len(json.dumps(payload.get("details")).encode("utf-8"))
    payload["details"] = {
        TRUNCATION_KEY: True,
        "original_bytes": original_bytes,
        "limit_bytes": MAX_EVENT_BYTES,
    }
    return {"timestamp": event["timestamp"], "message": json.dumps(payload)}


@register_audit_shipping_adapter("cloudwatch")
class CloudWatchAdapter(AuditShippingAdapter):
    """Ships audit rows to CloudWatch Logs.

    Config:
        log_group_name: Override the default FEOH_AUDIT_SHIPPING_CLOUDWATCH_GROUP.
        region_name:    Override the default aws region.
    """

    provider_name = "cloudwatch"

    def __init__(self, config: dict):
        super().__init__(config)
        self.log_group = config.get("log_group_name") or settings.audit_shipping_cloudwatch_group
        region = config.get("region_name") or "us-east-1"
        # endpoint_url=None → real CloudWatch; set FEOH_AWS_ENDPOINT_URL for LocalStack.
        self._client = boto3.client(
            "logs", region_name=region, endpoint_url=settings.aws_endpoint_url or None
        )
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
        # CloudWatch requires events ordered by timestamp ascending. Sort the
        # whole stream FIRST, then chunk — chunking a sorted list keeps every
        # call internally sorted and the calls themselves in order.
        events.sort(key=lambda e: e["timestamp"])
        for chunk in _chunk_events(events):
            response = self._client.put_log_events(
                logGroupName=self.log_group,
                logStreamName=stream_name,
                logEvents=chunk,
            )
            self._raise_on_rejected(response, len(chunk))

    @staticmethod
    def _raise_on_rejected(response: object, event_count: int) -> None:
        """Turn a 200-with-`rejectedLogEventsInfo` into a real failure.

        CloudWatch reports events it silently discarded (too old for the log
        group's retention, too far in the future) in the SUCCESS response. Left
        unread, the shipper stamps `shipped_at` on rows the WORM store never
        took. PII-free: only the index fields AWS returns, never a message.
        """
        if not isinstance(response, dict):
            return
        rejected = response.get("rejectedLogEventsInfo")
        if not rejected:
            return
        raise AuditShippingRejected(
            f"CloudWatch rejected events from a batch of {event_count}: {sorted(rejected)}"
        )

    # -- adapter API ---------------------------------------------------------

    async def ship(self, rows: list[AuditLogRow]) -> None:
        if not rows:
            return

        # Bucket rows into (tenant, YYYY-MM-DD) streams. `_put_events` chunks
        # each stream's events to the PutLogEvents caps; `_fit_event` shrinks a
        # single row that could never fit one call on its own.
        buckets: dict[str, list[dict]] = defaultdict(list)
        truncated = 0
        for row in rows:
            day = row.created_at.astimezone(UTC).strftime("%Y-%m-%d")
            stream = f"{row.tenant_db}/{day}"
            event = {
                "timestamp": int(row.created_at.timestamp() * 1000),
                "message": json.dumps(row.to_json()),
            }
            fitted = _fit_event(event, row)
            if fitted is not event:
                truncated += 1
            buckets[stream].append(fitted)

        if truncated:
            # Count only — never the row id, action or any of the dropped
            # details (this goes to the same log sink the PII rule governs).
            logger.warning(
                "[audit-shipping:cloudwatch] %d row(s) exceeded the %d-byte "
                "per-event cap; shipped with details replaced by a truncation "
                "marker (the full row remains in the tenant DB and the S3 copy)",
                truncated,
                MAX_EVENT_BYTES,
            )

        def _ship():
            self._ensure_log_group()
            for stream_name, events in buckets.items():
                self._ensure_log_stream(stream_name)
                self._put_events(stream_name, events)

        try:
            await asyncio.to_thread(_ship)
        except AuditShippingRejected:
            # Already PII-free and self-describing; the shipper counts it as a
            # failed tenant sweep and the rows stay unshipped.
            logger.error(
                "[audit-shipping:cloudwatch] CloudWatch discarded events from a "
                "batch of %d row(s); leaving them unshipped for retry",
                len(rows),
            )
            raise
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
