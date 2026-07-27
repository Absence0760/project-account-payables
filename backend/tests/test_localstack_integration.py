"""Live integration test for the AWS-backed paths against LocalStack.

Exercises the real adapters end-to-end: the cloudwatch + s3_objectlock audit
sinks and the SES email adapter, all pointed at a running LocalStack via
FEOH_AWS_ENDPOINT_URL, asserting the artifacts actually land.

Gated: the whole module is skipped unless FEOH_AWS_ENDPOINT_URL is set AND
LocalStack answers its health probe — so it runs locally after `pnpm aws:up`
(with the env from docs/local-aws-localstack.md) and in the CI e2e job, but is a
clean no-op in the default DB-free unit run. This is environment gating on an
optional dependency, not a masked failure: when LocalStack is present the
assertions are strict.

Run locally:
    pnpm aws:up
    FEOH_AWS_ENDPOINT_URL=http://localhost:4566 AWS_ACCESS_KEY_ID=test \
      AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1 \
      FEOH_AUDIT_SHIPPING_S3_BUCKET=ap-audit-worm \
      pytest tests/test_localstack_integration.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest

ENDPOINT = os.environ.get("FEOH_AWS_ENDPOINT_URL", "")
BUCKET = os.environ.get("FEOH_AUDIT_SHIPPING_S3_BUCKET", "ap-audit-worm")


def _localstack_up() -> bool:
    if not ENDPOINT:
        return False
    try:
        httpx.get(f"{ENDPOINT}/_localstack/health", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


_UP = _localstack_up()

# Locally this module skips when LocalStack isn't up, so a dev box without
# `pnpm aws:up` still runs the rest of the suite. But the CI service-e2e job
# starts LocalStack on purpose and sets FEOH_REQUIRE_INTEGRATION — there, an
# unreachable service is a hard failure, never a silent skip that leaves the
# job green with this coverage quietly dropped.
if not _UP and os.environ.get("FEOH_REQUIRE_INTEGRATION"):
    raise RuntimeError(
        "LocalStack is required (FEOH_REQUIRE_INTEGRATION is set) but was not "
        "reachable at FEOH_AWS_ENDPOINT_URL. The CI service-e2e job starts it "
        "on purpose; refusing to skip and drop coverage silently."
    )

pytestmark = [
    pytest.mark.skipif(
        not _UP,
        reason="LocalStack not configured/reachable — set FEOH_AWS_ENDPOINT_URL + `pnpm aws:up`",
    ),
    pytest.mark.asyncio,
]

# LocalStack accepts any credentials, but botocore still needs some present.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def _row() -> object:
    from app.services.audit_shipping.base import AuditLogRow

    return AuditLogRow(
        id=uuid.uuid4(),
        tenant_db="feoh_acme",
        organization_id=uuid.uuid4(),
        correlation_id=None,
        actor_id=None,
        action="invoice.approved",
        entity_type="invoice",
        entity_id=uuid.uuid4(),
        details={"amount": "100.00"},
        created_at=datetime.now(UTC),
    )


async def test_s3_objectlock_sink_writes_an_object():
    from app.services.audit_shipping.s3_objectlock_adapter import S3ObjectLockAdapter

    adapter = S3ObjectLockAdapter({"bucket_name": BUCKET})
    assert await adapter.test_connection() is True
    await adapter.ship([_row()])

    import boto3

    s3 = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1")
    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix="audit/")
    assert listing.get("KeyCount", 0) >= 1


async def test_cloudwatch_sink_creates_a_log_stream():
    from app.services.audit_shipping.cloudwatch_adapter import CloudWatchAdapter

    adapter = CloudWatchAdapter({"log_group_name": "/ap/audit"})
    assert await adapter.test_connection() is True
    await adapter.ship([_row()])

    import boto3

    logs = boto3.client("logs", endpoint_url=ENDPOINT, region_name="us-east-1")
    streams = logs.describe_log_streams(logGroupName="/ap/audit").get("logStreams", [])
    assert len(streams) >= 1


async def test_ses_adapter_sends_captured_mail():
    from app.services.email_adapters.base import EmailMessage
    from app.services.email_adapters.ses_adapter import SesAdapter

    before = httpx.get(f"{ENDPOINT}/_aws/ses", timeout=5.0).json().get("messages", [])
    adapter = SesAdapter({"from_address": "no-reply@localhost"})
    await adapter.send(
        EmailMessage(
            to="dev@acme.test",
            subject="LocalStack SES test",
            body_text="hi",
            body_html=None,
        )
    )
    after = httpx.get(f"{ENDPOINT}/_aws/ses", timeout=5.0).json().get("messages", [])
    assert len(after) == len(before) + 1
