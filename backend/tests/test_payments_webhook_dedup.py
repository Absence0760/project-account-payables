"""Webhook dedup edge case: an empty ``event_id`` must be an explicit,
logged branch — not a silent skip of the Redis dedup check.

Processor invariant #9 ("dedupe by event id") is layered:
  1. Redis SET-NX on the provider event id (first line).
  2. The terminal-state allowlist under a FOR UPDATE row lock (backstop).

When a (buggy / future) adapter returns a ``WebhookEvent`` with a falsy
``event_id``, line 1 can't run. The handler must still process the event
(the backstop makes that safe) AND emit a warning so an operator can spot
the adapter that stopped including ids — rather than silently short-
circuiting the dedup call with an ``if event.event_id and ...`` that reads
as "deduped" when it isn't.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.payment_adapters import PaymentStatus


def _fake_request(body: bytes = b"{}", headers: dict | None = None):
    req = MagicMock()
    req.body = AsyncMock(return_value=body)
    req.headers = headers or {}
    return req


def _ctrl_session_factory(org):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=org)
    ctrl_db = AsyncMock()
    ctrl_db.execute = AsyncMock(return_value=result)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=ctrl_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _tenant_session_factory(payment):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=payment)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, db


def _org(slug="acme", provider="modern_treasury"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug=slug,
        db_name=f"feoh_{slug}",
        settings={"payments": {"provider": provider, "webhook_signing_secret": "s3cret"}},
    )


def _in_flight_payment():
    return SimpleNamespace(
        id=uuid.uuid4(),
        provider_payment_id="px_1",
        payment_run_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        status="submitted",  # overwritable per the allowlist
        method="ach",
        amount=None,
        completed_at=None,
        reference=None,
        failure_reason=None,
    )


@pytest.mark.asyncio
async def test_empty_event_id_skips_redis_dedup_explicitly_and_warns(caplog):
    """A falsy ``event_id`` must NOT call ``is_event_already_processed`` (it
    can't dedup an empty id), must log a warning, and must still process the
    event through the backstop (settle the in-flight payment)."""
    import logging

    from app.api.payments import payment_webhook

    org = _org()
    payment = _in_flight_payment()
    payment.amount = Decimal("100.00")

    adapter = MagicMock()
    adapter.parse_webhook = MagicMock(
        return_value=SimpleNamespace(
            provider_payment_id="px_1",
            event_id="",  # the edge case
            status=PaymentStatus.completed,
            reference="REF-1",
            failure_reason=None,
        )
    )

    tenant_factory, db = _tenant_session_factory(payment)

    with (
        patch("app.database.control_session_factory", _ctrl_session_factory(org)),
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.database.get_tenant_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=tenant_factory),
        patch(
            "app.services.webhook_security.is_event_already_processed",
            new_callable=AsyncMock,
        ) as mk_dedup,
        patch("app.services.audit_dispatch.dispatch_audit", new_callable=AsyncMock),
        patch("app.services.payment_erp_sync.dispatch_payment_sync", new_callable=AsyncMock),
        caplog.at_level(logging.WARNING, logger="app.api.payments"),
    ):
        await payment_webhook(
            tenant_slug="acme", provider="modern_treasury", request=_fake_request()
        )

    # The dedup helper is NOT called for an empty id — no false "deduped".
    mk_dedup.assert_not_awaited()
    # An operator-visible warning fired.
    assert any("empty event_id" in r.message for r in caplog.records)
    # The event was still processed via the backstop path.
    assert payment.status == "completed"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_populated_event_id_still_deduped():
    """The normal path is unchanged: a populated event id runs the Redis
    dedup, and a replay (already-processed) short-circuits before any DB
    session is opened."""
    from app.api.payments import payment_webhook

    org = _org()
    adapter = MagicMock()
    adapter.parse_webhook = MagicMock(
        return_value=SimpleNamespace(
            provider_payment_id="px_1",
            event_id="evt_dup",
            status=PaymentStatus.completed,
            reference="REF-1",
            failure_reason=None,
        )
    )

    tenant_factory, db = _tenant_session_factory(_in_flight_payment())

    with (
        patch("app.database.control_session_factory", _ctrl_session_factory(org)),
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.database.get_tenant_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=tenant_factory),
        patch(
            "app.services.webhook_security.is_event_already_processed",
            new_callable=AsyncMock,
            return_value=True,  # replay
        ) as mk_dedup,
    ):
        await payment_webhook(
            tenant_slug="acme", provider="modern_treasury", request=_fake_request()
        )

    mk_dedup.assert_awaited_once()
    # Replay short-circuits: the tenant DB is never opened.
    db.execute.assert_not_called()
    db.commit.assert_not_called()
