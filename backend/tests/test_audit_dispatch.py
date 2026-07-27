"""Tests for audit_dispatch — the router that sends audit writes either to
the in-process ``log_action`` (local mode) or to SQS for the Lambda consumer.

DB-free: collaborators are mocked. Covers the paths the existing suite only
introspects (signature checks / source-greps) but never executes:

  * dispatch_audit local vs lambda routing
  * dispatch_auth_audit's real body — tenant session open + log_action write
    with entity_type='auth' and the entity_id→correlation_id fallback
  * the documented "auth available first" contract: an audit-infra failure is
    swallowed (logged at WARNING) so login/logout never fail
  * _resolve_tenant_db_name raising for an unknown org (tenant-isolation
    chokepoint for the lambda + auth-audit writes)
  * _send_to_sqs message-body shape, FIFO MessageGroupId, and the
    aws_endpoint_url → s3_endpoint_url fallback
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import audit_dispatch


def _kw(**overrides) -> dict:
    base = dict(
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        actor_id=uuid.uuid4(),
        action="payment.voided",
        entity_type="payment",
        entity_id=uuid.uuid4(),
        details={"reason": "test"},
    )
    base.update(overrides)
    return base


class _FakeSession:
    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False


class _FakeCtrl:
    """Async control-plane session returning a fixed scalar from execute()."""

    def __init__(self, scalar) -> None:
        self._scalar = scalar

    async def __aenter__(self) -> _FakeCtrl:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=self._scalar)
        return result


# ---------------------------------------------------------------------------
# dispatch_audit — routing
# ---------------------------------------------------------------------------


async def test_dispatch_audit_lambda_sends_to_sqs_not_local(monkeypatch):
    monkeypatch.setattr(audit_dispatch.settings, "audit_mode", "lambda")
    kw = _kw()
    log_action = AsyncMock()
    with (
        patch.object(audit_dispatch, "_resolve_tenant_db_name", AsyncMock(return_value="feoh_acme")),
        patch.object(audit_dispatch, "_send_to_sqs") as send,
        patch("app.services.audit.log_action", log_action),
    ):
        await audit_dispatch.dispatch_audit(MagicMock(), **kw)

    send.assert_called_once()
    assert send.call_args.kwargs["tenant_db_name"] == "feoh_acme"
    assert send.call_args.kwargs["action"] == "payment.voided"
    log_action.assert_not_awaited()


async def test_dispatch_audit_local_delegates_to_log_action(monkeypatch):
    monkeypatch.setattr(audit_dispatch.settings, "audit_mode", "local")
    kw = _kw()
    db = MagicMock()
    log_action = AsyncMock()
    with (
        patch("app.services.audit.log_action", log_action),
        patch.object(audit_dispatch, "_send_to_sqs") as send,
    ):
        await audit_dispatch.dispatch_audit(db, **kw)

    send.assert_not_called()
    log_action.assert_awaited_once()
    assert log_action.await_args.args[0] is db
    for key, val in kw.items():
        assert log_action.await_args.kwargs[key] == val


# ---------------------------------------------------------------------------
# dispatch_auth_audit — real body + error swallow
# ---------------------------------------------------------------------------


async def test_dispatch_auth_audit_writes_tenant_audit_row(monkeypatch):
    monkeypatch.setattr(audit_dispatch.settings, "audit_mode", "local")
    session = _FakeSession()
    log_action = AsyncMock()
    org_id, actor_id = uuid.uuid4(), uuid.uuid4()
    with (
        patch.object(audit_dispatch, "_resolve_tenant_db_name", AsyncMock(return_value="feoh_acme")),
        patch("app.database.get_tenant_engine", MagicMock(return_value=MagicMock())),
        patch.object(audit_dispatch, "async_sessionmaker", MagicMock(return_value=lambda: session)),
        patch("app.services.audit.log_action", log_action),
    ):
        await audit_dispatch.dispatch_auth_audit(
            organization_id=org_id,
            actor_id=actor_id,
            action="user.login",
            entity_id=None,
        )

    log_action.assert_awaited_once()
    kwargs = log_action.await_args.kwargs
    assert kwargs["entity_type"] == "auth"
    assert kwargs["organization_id"] == org_id
    assert kwargs["actor_id"] == actor_id
    # entity_id was None → falls back to the generated correlation_id.
    assert kwargs["entity_id"] == kwargs["correlation_id"]
    session.commit.assert_awaited_once()


async def test_dispatch_auth_audit_swallows_errors_so_auth_never_fails(monkeypatch, caplog):
    monkeypatch.setattr(audit_dispatch.settings, "audit_mode", "local")
    with patch.object(
        audit_dispatch,
        "_resolve_tenant_db_name",
        AsyncMock(side_effect=ValueError("org gone")),
    ):
        with caplog.at_level(logging.WARNING):
            # Must NOT raise — auth availability outranks audit observability.
            result = await audit_dispatch.dispatch_auth_audit(
                organization_id=uuid.uuid4(),
                actor_id=None,
                action="user.login",
            )
    assert result is None
    assert any("auth audit dispatch failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _resolve_tenant_db_name — tenant-isolation chokepoint
# ---------------------------------------------------------------------------


async def test_resolve_tenant_db_name_returns_name_for_known_org():
    factory = MagicMock(return_value=_FakeCtrl("feoh_acme"))
    with patch("app.database.control_session_factory", factory):
        name = await audit_dispatch._resolve_tenant_db_name(uuid.uuid4())
    assert name == "feoh_acme"


async def test_resolve_tenant_db_name_raises_for_unknown_org():
    with patch("app.database.control_session_factory", MagicMock(return_value=_FakeCtrl(None))):
        with pytest.raises(ValueError, match="not found"):
            await audit_dispatch._resolve_tenant_db_name(uuid.uuid4())


# ---------------------------------------------------------------------------
# _send_to_sqs — message shape + endpoint override
# ---------------------------------------------------------------------------


@contextmanager
def _sqs_settings(monkeypatch, *, aws_endpoint_url, s3_endpoint_url="http://s3:4566"):
    monkeypatch.setattr(audit_dispatch.settings, "aws_endpoint_url", aws_endpoint_url)
    monkeypatch.setattr(audit_dispatch.settings, "s3_endpoint_url", s3_endpoint_url)
    monkeypatch.setattr(audit_dispatch.settings, "s3_access_key", "ak")
    monkeypatch.setattr(audit_dispatch.settings, "s3_secret_key", "sk")
    monkeypatch.setattr(audit_dispatch.settings, "sqs_audit_queue_url", "http://q/audit.fifo")
    yield


def test_send_to_sqs_serializes_body_and_sets_group_id(monkeypatch):
    corr = uuid.uuid4()
    org = uuid.uuid4()
    ent = uuid.uuid4()
    client = MagicMock()
    with _sqs_settings(monkeypatch, aws_endpoint_url="http://aws:4566"):
        with patch.object(audit_dispatch.boto3, "client", MagicMock(return_value=client)) as mk:
            audit_dispatch._send_to_sqs(
                tenant_db_name="feoh_acme",
                correlation_id=corr,
                organization_id=org,
                actor_id=None,
                action="user.login",
                entity_type="auth",
                entity_id=ent,
                details={"ip": "1.2.3.4"},
            )

    # aws_endpoint_url takes precedence over s3_endpoint_url.
    assert mk.call_args.kwargs["endpoint_url"] == "http://aws:4566"
    send = client.send_message.call_args.kwargs
    assert send["QueueUrl"] == "http://q/audit.fifo"
    assert send["MessageGroupId"] == str(corr)
    body = json.loads(send["MessageBody"])
    assert body == {
        "tenant_db_name": "feoh_acme",
        "correlation_id": str(corr),
        "organization_id": str(org),
        "actor_id": None,
        "action": "user.login",
        "entity_type": "auth",
        "entity_id": str(ent),
        "details": {"ip": "1.2.3.4"},
    }


def test_send_to_sqs_falls_back_to_s3_endpoint(monkeypatch):
    client = MagicMock()
    with _sqs_settings(monkeypatch, aws_endpoint_url=None, s3_endpoint_url="http://s3:4566"):
        with patch.object(audit_dispatch.boto3, "client", MagicMock(return_value=client)) as mk:
            audit_dispatch._send_to_sqs(
                tenant_db_name="feoh_acme",
                correlation_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                actor_id=uuid.uuid4(),
                action="user.login",
                entity_type="auth",
                entity_id=uuid.uuid4(),
                details=None,
            )
    assert mk.call_args.kwargs["endpoint_url"] == "http://s3:4566"
