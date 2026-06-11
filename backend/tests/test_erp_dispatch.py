"""Tests for erp_dispatch routing — the inverse of test_extraction_dispatch's
lambda-routing test, which erp_dispatch lacked despite identical structure.

DB-free. Covers:
  * dispatch_erp routes to SQS in lambda mode and never spawns a worker thread
  * dispatch_erp starts the in-process thread in local mode and never touches SQS
  * _send_to_sqs message body + FIFO MessageGroupId shape (a silent Lambda
    payload regression double-posts to the ERP, so the contract is pinned)

The aws_endpoint_url → s3_endpoint_url fallback for this module's _send_to_sqs
is pinned alongside the extraction variant in test_aws_endpoint_override.py.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

from app.config import settings
from app.services import erp_dispatch


async def test_dispatch_erp_lambda_routes_to_sqs_not_thread(monkeypatch):
    monkeypatch.setattr(settings, "erp_mode", "lambda")
    with (
        patch.object(erp_dispatch, "_send_to_sqs") as send,
        patch.object(erp_dispatch.threading, "Thread") as thread,
    ):
        await erp_dispatch.dispatch_erp(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    send.assert_called_once()
    thread.assert_not_called()


async def test_dispatch_erp_local_starts_thread_not_sqs(monkeypatch):
    monkeypatch.setattr(settings, "erp_mode", "local")
    with (
        patch.object(erp_dispatch, "_send_to_sqs") as send,
        patch.object(erp_dispatch.threading, "Thread") as thread,
    ):
        await erp_dispatch.dispatch_erp(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    send.assert_not_called()
    thread.assert_called_once()
    thread.return_value.start.assert_called_once()


def test_send_to_sqs_message_body_and_group_id_shape(monkeypatch):
    invoice_id, org_id, actor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(settings, "aws_endpoint_url", "http://aws:4566")
    monkeypatch.setattr(settings, "s3_access_key", "ak")
    monkeypatch.setattr(settings, "s3_secret_key", "sk")
    monkeypatch.setattr(settings, "sqs_erp_queue_url", "http://q/erp.fifo")

    client = MagicMock()
    with patch.object(erp_dispatch.boto3, "client", MagicMock(return_value=client)):
        erp_dispatch._send_to_sqs(invoice_id, org_id, actor_id)

    send = client.send_message.call_args.kwargs
    assert send["QueueUrl"] == "http://q/erp.fifo"
    # FIFO ordering keyed on the invoice — relies on content-based dedup.
    assert send["MessageGroupId"] == str(invoice_id)
    body = json.loads(send["MessageBody"])
    assert body == {
        "invoice_id": str(invoice_id),
        "org_id": str(org_id),
        "actor_id": str(actor_id),
    }
