"""SES adapter must keep PII out of its failure logs.

A send failure used to log `"SES send failed for %s: %s", message.to, exc` —
the recipient address plus the raw exception (AWS SDK errors can embed
sender/recipient addresses). The recipient is PII and must not reach the log
sink. The adapter now logs only the exception class name.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from botocore.exceptions import ClientError

from app.services.email_adapters.base import EmailMessage
from app.services.email_adapters.ses_adapter import SesAdapter


def _adapter(monkeypatch):
    # Avoid constructing a real boto3 client / hitting AWS.
    adapter = SesAdapter.__new__(SesAdapter)
    adapter.config = {"from_address": "noreply@example.com"}

    class _BoomClient:
        def send_email(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "MessageRejected", "Message": "boom"}}, "SendEmail"
            )

    adapter._client = _BoomClient()
    return adapter


def test_send_failure_does_not_log_recipient(monkeypatch, caplog):
    adapter = _adapter(monkeypatch)
    msg = EmailMessage(
        to="vendor-secret@example.com",
        subject="Invoice paid",
        body_text="hello",
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ClientError):
            asyncio.run(adapter.send(msg))

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    # The recipient address must NOT appear anywhere in the log output.
    assert "vendor-secret@example.com" not in log_text
    # Only the exception class name is recorded.
    assert "ClientError" in log_text
