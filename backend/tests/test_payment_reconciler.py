"""Tests for the payment-status reconciliation sweeper.

The full multi-tenant scan is exercised via the e2e harness; these
tests pin the per-tenant decision logic — when a payment is too young
to poll, when the adapter raise is swallowed, when the upstream
status is accepted, and when the absolute max-age fallback kicks in.

We exercise `_reconcile_tenant` indirectly by stubbing out the
async-engine setup and feeding the inner loop directly. That keeps
the test DB-free without losing coverage of the decision branch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _payment(*, status="submitted", submitted_at=None, provider_payment_id="px_123"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        submitted_at=submitted_at or (datetime.now(UTC) - timedelta(hours=1)),
        provider_payment_id=provider_payment_id,
        completed_at=None,
        failure_reason=None,
    )


def _result_with(payments):
    """Mock the SQLAlchemy result chain for `select(Payment).where(...)`."""
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=payments)
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    return result


@pytest.mark.asyncio
async def test_reconciler_skips_payments_younger_than_settle_after():
    """Polling a payment that's only been in flight for 30s is wasteful;
    the loop should leave young rows alone."""
    from app.services.payment_reconciler import _reconcile_tenant

    org = SimpleNamespace(
        id=uuid.uuid4(),
        db_name="ap_acme",
        settings={"payments": {"provider": "mock"}},
    )

    young = _payment(submitted_at=datetime.now(UTC) - timedelta(seconds=30))
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=_result_with([young]))

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=fake_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.payment_reconciler.create_async_engine") as mk_engine,
        patch("app.services.payment_reconciler.async_sessionmaker", return_value=factory),
        patch("app.services.payment_reconciler.get_payment_adapter") as mk_adapter,
    ):
        mk_engine.return_value = MagicMock(dispose=AsyncMock())
        adapter = MagicMock()
        adapter.get_payment_status = AsyncMock()
        mk_adapter.return_value = adapter

        outcome = await _reconcile_tenant(org, datetime.now(UTC))

    assert outcome["polled"] == 0
    adapter.get_payment_status.assert_not_called()


@pytest.mark.asyncio
async def test_reconciler_accepts_terminal_status_from_adapter():
    """Older `submitted` payment whose processor reports `completed`
    flips locally to `completed` + completed_at."""
    from app.services.payment_adapters import PaymentStatus
    from app.services.payment_reconciler import _reconcile_tenant

    org = SimpleNamespace(
        id=uuid.uuid4(),
        db_name="ap_acme",
        settings={"payments": {"provider": "mock"}},
    )
    old = _payment(submitted_at=datetime.now(UTC) - timedelta(hours=2))
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=_result_with([old]))

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=fake_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.payment_reconciler.create_async_engine") as mk_engine,
        patch("app.services.payment_reconciler.async_sessionmaker", return_value=factory),
        patch("app.services.payment_reconciler.get_payment_adapter") as mk_adapter,
    ):
        mk_engine.return_value = MagicMock(dispose=AsyncMock())
        adapter = MagicMock()
        adapter.get_payment_status = AsyncMock(return_value=PaymentStatus.completed)
        adapter.provider_name = "mock"
        mk_adapter.return_value = adapter

        outcome = await _reconcile_tenant(org, datetime.now(UTC))

    assert outcome["polled"] == 1
    assert outcome["resolved"] == 1
    assert old.status == "completed"
    assert old.completed_at is not None
    fake_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_reconciler_aged_out_payments_flip_to_failed():
    """Past max age, the row is force-failed regardless of upstream
    status. Operators investigate via audit log."""
    from app.services.payment_reconciler import _reconcile_tenant

    org = SimpleNamespace(
        id=uuid.uuid4(),
        db_name="ap_acme",
        settings={"payments": {"provider": "mock"}},
    )
    ancient = _payment(submitted_at=datetime.now(UTC) - timedelta(days=10))
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=_result_with([ancient]))

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=fake_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.payment_reconciler.create_async_engine") as mk_engine,
        patch("app.services.payment_reconciler.async_sessionmaker", return_value=factory),
        patch("app.services.payment_reconciler.get_payment_adapter") as mk_adapter,
    ):
        mk_engine.return_value = MagicMock(dispose=AsyncMock())
        adapter = MagicMock()
        adapter.get_payment_status = AsyncMock()  # should not be called
        mk_adapter.return_value = adapter

        outcome = await _reconcile_tenant(org, datetime.now(UTC))

    assert outcome["aged_out"] == 1
    assert ancient.status == "failed"
    assert "max_age_exceeded" in (ancient.failure_reason or "")
    adapter.get_payment_status.assert_not_called()


@pytest.mark.asyncio
async def test_reconciler_swallows_adapter_exceptions():
    """A processor outage shouldn't break the sweep — log and move on,
    leaving the row in `submitted` for the next pass."""
    from app.services.payment_reconciler import _reconcile_tenant

    org = SimpleNamespace(
        id=uuid.uuid4(),
        db_name="ap_acme",
        settings={"payments": {"provider": "mock"}},
    )
    old = _payment(submitted_at=datetime.now(UTC) - timedelta(hours=2))
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=_result_with([old]))

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=fake_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.payment_reconciler.create_async_engine") as mk_engine,
        patch("app.services.payment_reconciler.async_sessionmaker", return_value=factory),
        patch("app.services.payment_reconciler.get_payment_adapter") as mk_adapter,
    ):
        mk_engine.return_value = MagicMock(dispose=AsyncMock())
        adapter = MagicMock()
        adapter.provider_name = "mock"
        adapter.get_payment_status = AsyncMock(side_effect=RuntimeError("processor 503"))
        mk_adapter.return_value = adapter

        outcome = await _reconcile_tenant(org, datetime.now(UTC))

    assert outcome["polled"] == 1
    assert outcome["resolved"] == 0
    assert old.status == "submitted"  # unchanged


@pytest.mark.asyncio
async def test_reconciler_skips_org_without_payment_provider():
    """An org that hasn't configured a processor has no payments to
    reconcile against. The sweeper short-circuits before touching the
    tenant DB."""
    from app.services.payment_reconciler import _reconcile_tenant

    org = SimpleNamespace(
        id=uuid.uuid4(),
        db_name="ap_acme",
        settings={"payments": {}},
    )

    with patch("app.services.payment_reconciler.create_async_engine") as mk_engine:
        outcome = await _reconcile_tenant(org, datetime.now(UTC))

    assert outcome == {"polled": 0, "resolved": 0, "aged_out": 0}
    mk_engine.assert_not_called()
