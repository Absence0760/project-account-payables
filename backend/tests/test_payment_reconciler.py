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


# ---------------------------------------------------------------------------
# Multi-tenant orchestration (`reconcile_once`)
#
# The per-tenant decision logic is covered above; these tests pin the
# orchestration: aggregation across tenants, per-tenant failure isolation,
# and the failures counter.
# ---------------------------------------------------------------------------


def _ctrl_factory_returning(orgs):
    """Mock the control-plane session factory used by reconcile_once."""
    ctrl_result = MagicMock()
    ctrl_scalars = MagicMock()
    ctrl_scalars.all = MagicMock(return_value=orgs)
    ctrl_result.scalars = MagicMock(return_value=ctrl_scalars)
    ctrl_db = AsyncMock()
    ctrl_db.execute = AsyncMock(return_value=ctrl_result)

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=ctrl_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


@pytest.mark.asyncio
async def test_reconcile_once_aggregates_outcomes_across_tenants():
    """Counters from each `_reconcile_tenant` call accumulate onto the
    top-level `ReconcileResult`."""
    from app.services.payment_reconciler import reconcile_once

    orgs = [
        SimpleNamespace(
            id=uuid.uuid4(), db_name="ap_a", settings={"payments": {"provider": "mock"}}
        ),
        SimpleNamespace(
            id=uuid.uuid4(), db_name="ap_b", settings={"payments": {"provider": "mock"}}
        ),
        SimpleNamespace(
            id=uuid.uuid4(), db_name="ap_c", settings={"payments": {"provider": "mock"}}
        ),
    ]

    per_tenant = AsyncMock(
        side_effect=[
            {"polled": 2, "resolved": 1, "aged_out": 0},
            {"polled": 5, "resolved": 3, "aged_out": 1},
            {"polled": 0, "resolved": 0, "aged_out": 0},
        ]
    )

    with (
        patch(
            "app.services.payment_reconciler.control_session_factory",
            _ctrl_factory_returning(orgs),
        ),
        patch("app.services.payment_reconciler._reconcile_tenant", per_tenant),
    ):
        result = await reconcile_once()

    assert result.tenants_scanned == 3
    assert result.payments_polled == 7
    assert result.payments_resolved == 4
    assert result.payments_aged_out == 1
    assert result.failures == 0
    assert per_tenant.await_count == 3


@pytest.mark.asyncio
async def test_reconcile_once_isolates_per_tenant_failures():
    """One tenant blowing up must not abort the rest of the sweep — the
    failures counter increments and the other tenants are still polled."""
    from app.services.payment_reconciler import reconcile_once

    orgs = [
        SimpleNamespace(
            id=uuid.uuid4(), db_name="ap_good", settings={"payments": {"provider": "mock"}}
        ),
        SimpleNamespace(
            id=uuid.uuid4(), db_name="ap_bad", settings={"payments": {"provider": "mock"}}
        ),
        SimpleNamespace(
            id=uuid.uuid4(), db_name="ap_also_good", settings={"payments": {"provider": "mock"}}
        ),
    ]

    per_tenant = AsyncMock(
        side_effect=[
            {"polled": 1, "resolved": 1, "aged_out": 0},
            RuntimeError("connection refused"),
            {"polled": 2, "resolved": 0, "aged_out": 1},
        ]
    )

    with (
        patch(
            "app.services.payment_reconciler.control_session_factory",
            _ctrl_factory_returning(orgs),
        ),
        patch("app.services.payment_reconciler._reconcile_tenant", per_tenant),
    ):
        result = await reconcile_once()

    assert result.tenants_scanned == 3
    assert result.failures == 1
    # The good tenants' counts still made it in.
    assert result.payments_polled == 3
    assert result.payments_resolved == 1
    assert result.payments_aged_out == 1


@pytest.mark.asyncio
async def test_reconcile_once_handles_empty_tenant_list():
    """No orgs in the control plane — the sweep is a no-op, not a crash."""
    from app.services.payment_reconciler import reconcile_once

    with patch(
        "app.services.payment_reconciler.control_session_factory",
        _ctrl_factory_returning([]),
    ):
        result = await reconcile_once()

    assert result.tenants_scanned == 0
    assert result.payments_polled == 0
    assert result.failures == 0


@pytest.mark.asyncio
async def test_reconcile_once_passes_caller_supplied_now_through():
    """The `now` argument must reach `_reconcile_tenant` unchanged so a
    CLI-invoked sweep can pin its own wall clock for replay / tests."""
    from app.services.payment_reconciler import reconcile_once

    orgs = [
        SimpleNamespace(
            id=uuid.uuid4(), db_name="ap_a", settings={"payments": {"provider": "mock"}}
        ),
    ]
    fixed_now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
    per_tenant = AsyncMock(return_value={"polled": 0, "resolved": 0, "aged_out": 0})

    with (
        patch(
            "app.services.payment_reconciler.control_session_factory",
            _ctrl_factory_returning(orgs),
        ),
        patch("app.services.payment_reconciler._reconcile_tenant", per_tenant),
    ):
        await reconcile_once(now=fixed_now)

    args, _kwargs = per_tenant.call_args
    assert args[1] == fixed_now
