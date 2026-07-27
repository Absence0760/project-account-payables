"""The AWS-backed clients must honor FEOH_AWS_ENDPOINT_URL (LocalStack).

When `aws_endpoint_url` is set, the SES / CloudWatch Logs / S3 Object Lock
clients and the SQS dispatch client must be built with that `endpoint_url`; when
it's empty they must pass `endpoint_url=None` (→ real AWS) so production is
unaffected. These are the seams that let the whole AWS surface run against the
local LocalStack container (see docs/local-aws-localstack.md).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.config import settings

LOCAL = "http://localhost:4566"


@pytest.fixture
def captured_client(monkeypatch):
    """Intercept boto3.client and record the kwargs of the last call."""
    calls: list[dict] = []

    def _fake_client(service, **kwargs):
        calls.append({"service": service, **kwargs})
        return MagicMock()

    monkeypatch.setattr("boto3.client", _fake_client)
    return calls


def test_ses_adapter_uses_endpoint_when_set(captured_client, monkeypatch):
    from app.services.email_adapters.ses_adapter import SesAdapter

    monkeypatch.setattr(settings, "aws_endpoint_url", LOCAL)
    SesAdapter({"from_address": "no-reply@localhost"})
    assert captured_client[-1]["service"] == "ses"
    assert captured_client[-1]["endpoint_url"] == LOCAL


def test_ses_adapter_endpoint_none_when_empty(captured_client, monkeypatch):
    from app.services.email_adapters.ses_adapter import SesAdapter

    monkeypatch.setattr(settings, "aws_endpoint_url", "")
    SesAdapter({"from_address": "no-reply@localhost"})
    assert captured_client[-1]["endpoint_url"] is None


def test_cloudwatch_adapter_uses_endpoint_when_set(captured_client, monkeypatch):
    from app.services.audit_shipping.cloudwatch_adapter import CloudWatchAdapter

    monkeypatch.setattr(settings, "aws_endpoint_url", LOCAL)
    CloudWatchAdapter({})
    assert captured_client[-1]["service"] == "logs"
    assert captured_client[-1]["endpoint_url"] == LOCAL


def test_s3_objectlock_adapter_uses_endpoint_when_set(captured_client, monkeypatch):
    from app.services.audit_shipping.s3_objectlock_adapter import S3ObjectLockAdapter

    monkeypatch.setattr(settings, "aws_endpoint_url", LOCAL)
    S3ObjectLockAdapter({"bucket_name": "ap-audit-worm"})
    assert captured_client[-1]["service"] == "s3"
    assert captured_client[-1]["endpoint_url"] == LOCAL


def test_sqs_dispatch_prefers_aws_endpoint(captured_client, monkeypatch):
    """SQS dispatch falls back to s3_endpoint_url for back-compat, but
    aws_endpoint_url wins when set (the LocalStack path)."""
    from app.services import extraction_dispatch

    monkeypatch.setattr(settings, "aws_endpoint_url", LOCAL)
    extraction_dispatch._send_to_sqs(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert captured_client[-1]["service"] == "sqs"
    assert captured_client[-1]["endpoint_url"] == LOCAL


def test_sqs_dispatch_falls_back_to_s3_endpoint(captured_client, monkeypatch):
    from app.services import extraction_dispatch

    monkeypatch.setattr(settings, "aws_endpoint_url", "")
    extraction_dispatch._send_to_sqs(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert captured_client[-1]["endpoint_url"] == settings.s3_endpoint_url


def test_erp_sqs_dispatch_prefers_aws_endpoint(captured_client, monkeypatch):
    """erp_dispatch._send_to_sqs shares the same fallback as extraction —
    pin it so a LocalStack-vs-prod regression is caught for both."""
    from app.services import erp_dispatch

    monkeypatch.setattr(settings, "aws_endpoint_url", LOCAL)
    erp_dispatch._send_to_sqs(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert captured_client[-1]["service"] == "sqs"
    assert captured_client[-1]["endpoint_url"] == LOCAL


def test_erp_sqs_dispatch_falls_back_to_s3_endpoint(captured_client, monkeypatch):
    from app.services import erp_dispatch

    monkeypatch.setattr(settings, "aws_endpoint_url", "")
    erp_dispatch._send_to_sqs(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert captured_client[-1]["endpoint_url"] == settings.s3_endpoint_url
