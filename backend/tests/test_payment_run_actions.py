"""Tests for the three new payment-run action endpoints.

These pin the state-machine guards (the part most likely to regress as
new statuses are added). The happy paths get e2e coverage; here we
focus on the 409 / 404 branches that protect the money-movement
invariants in CLAUDE.md (void must be guarded; approve must check the
CFO gate; cancel must refuse non-draft runs).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _db_returning_scalar(value):
    """Mock an AsyncSession whose `execute().scalar_one_or_none()`
    returns `value` on the first call."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    session.execute = AsyncMock(return_value=result)
    return session


def _db_returning_row(row):
    """Mock an AsyncSession whose `execute().one_or_none()` returns
    `row` on the first call (used for the join in void_payment)."""
    session = AsyncMock()
    result = MagicMock()
    result.one_or_none = MagicMock(return_value=row)
    session.execute = AsyncMock(return_value=result)
    return session


def _user(role="admin"):
    return SimpleNamespace(id=uuid.uuid4(), full_name="Test User", roles=[role])


def _org():
    return SimpleNamespace(id=uuid.uuid4(), settings={"payments": {"provider": "mock"}})


# ---------------------------------------------------------------------------
# void_payment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_void_payment_returns_404_for_unknown_id():
    from app.api.payments import VoidPaymentRequest, void_payment

    db = _db_returning_row(None)

    with pytest.raises(HTTPException) as exc:
        await void_payment(
            payment_id=uuid.uuid4(),
            body=VoidPaymentRequest(reason="test"),
            db=db,
            org=_org(),
            user=_user(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_void_payment_refuses_already_voided():
    """Idempotency from the user's side: a second click must not move
    money or write a duplicate audit row — it returns 409."""
    from app.api.payments import VoidPaymentRequest, void_payment

    payment = SimpleNamespace(
        id=uuid.uuid4(),
        status="voided",
        provider_payment_id="px_1",
        amount=Decimal("100"),
        correlation_id=uuid.uuid4(),
        completed_at=None,
    )
    db = _db_returning_row((payment, None))

    with pytest.raises(HTTPException) as exc:
        await void_payment(
            payment_id=payment.id,
            body=VoidPaymentRequest(reason="test"),
            db=db,
            org=_org(),
            user=_user(),
        )
    assert exc.value.status_code == 409
    assert "already" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_void_payment_refuses_failed_payment():
    """A `failed` payment never settled, so there's nothing to reverse.
    Voiding it would corrupt the books — the operator should retry the
    payment instead."""
    from app.api.payments import VoidPaymentRequest, void_payment

    payment = SimpleNamespace(
        id=uuid.uuid4(),
        status="failed",
        provider_payment_id=None,
        amount=Decimal("100"),
        correlation_id=uuid.uuid4(),
        completed_at=None,
    )
    db = _db_returning_row((payment, None))

    with pytest.raises(HTTPException) as exc:
        await void_payment(
            payment_id=payment.id,
            body=VoidPaymentRequest(reason="test"),
            db=db,
            org=_org(),
            user=_user(),
        )
    assert exc.value.status_code == 409
    assert "failed" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_void_payment_returns_invoice_to_approved_via_transition_invoice():
    """Happy path: a scheduled payment is voided, the invoice flips back
    to `approved`, and an audit row is dispatched. Pins both halves of
    the void-back-edge contract — the status change and the SOC 2 row.
    """
    from unittest.mock import patch

    from app.api.payments import VoidPaymentRequest, void_payment
    from app.models.invoice import InvoiceStatus

    payment_id = uuid.uuid4()
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    payment = SimpleNamespace(
        id=payment_id,
        invoice_id=uuid.uuid4(),
        payment_run_id=None,
        status="completed",
        provider_payment_id="px_1",
        amount=Decimal("250"),
        method="ach",
        reference="ref-1",
        correlation_id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        completed_at=None,
        failure_reason=None,
    )
    invoice = SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        invoice_number="INV-1",
        vendor_name="Acme",
        status=InvoiceStatus.payment_scheduled,
    )
    db = _db_returning_row((payment, invoice))
    db.refresh = AsyncMock()

    # No upstream adapter for this test — assert it's the invoice path
    # that's wired up correctly. `dispatch_audit` is imported inline
    # inside the handler, so it has to be patched at its source module.
    with (
        patch("app.api.payments.get_payment_adapter", return_value=SimpleNamespace()),
        patch("app.api.payments.transition_invoice", new_callable=AsyncMock) as ti,
        patch("app.services.audit_dispatch.dispatch_audit", new_callable=AsyncMock) as da,
    ):
        ti.return_value = invoice
        da.return_value = None
        await void_payment(
            payment_id=payment_id,
            body=VoidPaymentRequest(reason="duplicate"),
            db=db,
            org=_org(),
            user=_user(),
        )

    # The void back-edge must call transition_invoice for the invoice
    # status change, not assign invoice.status directly.
    ti.assert_awaited_once()
    call_kwargs = ti.call_args.kwargs
    assert call_kwargs.get("action_name") == "invoice.voided_return_to_approved"
    assert ti.call_args.args[2] == InvoiceStatus.approved

    # And the payment.voided audit row is dispatched alongside.
    da.assert_awaited_once()


# ---------------------------------------------------------------------------
# cancel_payment_run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_run_returns_404_for_unknown_run():
    from app.api.payments import cancel_payment_run

    db = _db_returning_scalar(None)

    with pytest.raises(HTTPException) as exc:
        await cancel_payment_run(
            run_id=uuid.uuid4(),
            db=db,
            org=_org(),
            user=_user(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_run_refuses_executed_run():
    """Once a run has executed, payments have moved upstream — the only
    way to unwind is per-payment void. The endpoint refuses with 409."""
    from app.api.payments import cancel_payment_run

    run = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        total_amount=Decimal("500"),
    )
    db = _db_returning_scalar(run)

    with pytest.raises(HTTPException) as exc:
        await cancel_payment_run(
            run_id=run.id,
            db=db,
            org=_org(),
            user=_user(),
        )
    assert exc.value.status_code == 409
    assert "draft" in exc.value.detail.lower()


# ---------------------------------------------------------------------------
# approve_payment_run (CFO sign-off)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_run_returns_404_for_unknown_run():
    from app.api.payments import approve_payment_run

    db = _db_returning_scalar(None)

    with pytest.raises(HTTPException) as exc:
        await approve_payment_run(
            run_id=uuid.uuid4(),
            db=db,
            org=_org(),
            user=_user(role="cfo"),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_approve_run_refuses_run_below_cfo_threshold():
    """A draft run that doesn't trip the CFO threshold has
    `requires_cfo_approval=False` — calling `/approve` on it makes no
    sense and would let a CFO short-circuit the standard executor."""
    from app.api.payments import approve_payment_run

    run = SimpleNamespace(
        id=uuid.uuid4(),
        status="draft",
        requires_cfo_approval=False,
        cfo_approved_at=None,
        total_amount=Decimal("100"),
    )
    db = _db_returning_scalar(run)

    with pytest.raises(HTTPException) as exc:
        await approve_payment_run(
            run_id=run.id,
            db=db,
            org=_org(),
            user=_user(role="cfo"),
        )
    assert exc.value.status_code == 409
    assert "does not require" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_approve_run_refuses_already_approved_run():
    """Re-approving doesn't break correctness, but it would re-stamp
    `cfo_approved_at` and rewrite the audit trail — refuse with 409."""
    from datetime import UTC, datetime

    from app.api.payments import approve_payment_run

    run = SimpleNamespace(
        id=uuid.uuid4(),
        status="draft",
        requires_cfo_approval=True,
        cfo_approved_at=datetime.now(UTC),
        total_amount=Decimal("10000"),
    )
    db = _db_returning_scalar(run)

    with pytest.raises(HTTPException) as exc:
        await approve_payment_run(
            run_id=run.id,
            db=db,
            org=_org(),
            user=_user(role="cfo"),
        )
    assert exc.value.status_code == 409
    assert "already" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_approve_run_refuses_non_draft_status():
    """A run that's already executed (or cancelled) can't be approved —
    the CFO gate is a pre-execute check, not a retroactive sign-off."""
    from app.api.payments import approve_payment_run

    run = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        requires_cfo_approval=True,
        cfo_approved_at=None,
        total_amount=Decimal("10000"),
    )
    db = _db_returning_scalar(run)

    with pytest.raises(HTTPException) as exc:
        await approve_payment_run(
            run_id=run.id,
            db=db,
            org=_org(),
            user=_user(role="cfo"),
        )
    assert exc.value.status_code == 409
    assert "draft" in exc.value.detail.lower()
