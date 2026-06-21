"""End-to-end payment-run execution flow.

`test_payment_run_actions.py` covers the guard-level rejections (404 /
409 on void / cancel / approve). This file covers the *happy paths*:

  - All payments in a run settle synchronously → run status `completed`
  - All payments fail at the adapter → run status `failed`
  - Mixed completed + failed → run status `partial`
  - All payments come back as in-flight (submitted/processing) → run
    status `submitted` (webhook will finalize)
  - Per-payment fields (provider_payment_id, reference, submitted_at,
    completed_at) populate correctly
  - The invoice status flips to `payment_scheduled` for any
    successful or in-flight payment
  - ERP-sync dispatch fires only when at least one payment newly
    completed
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.invoice import InvoiceStatus
from app.services.payment_adapters import PaymentStatus


@pytest.fixture(autouse=True)
def _clear_compliance_gate():
    """These flow tests exercise rollups/hydration, not the sanctions gate.
    Patch `check_payment_compliance` to a clear verdict so every payment with a
    (now-required) vendor proceeds to the adapter rather than holding."""
    with patch(
        "app.services.compliance.check_payment_compliance",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(verdict="allow", reasons=[]),
    ):
        yield


def _run(*, status: str = "draft", requires_cfo: bool = False, cfo_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        total_amount=Decimal("1000.00"),
        organization_id=uuid.uuid4(),
        initiated_by=uuid.uuid4(),
        requires_cfo_approval=requires_cfo,
        cfo_approved_at=cfo_at,
        cfo_approved_by=None,
        executed_at=None,
    )


def _payment(*, method="ach", amount: Decimal = Decimal("100.00")):
    return SimpleNamespace(
        id=uuid.uuid4(),
        payment_run_id=None,
        invoice_id=uuid.uuid4(),
        amount=amount,
        method=method,
        status="pending",
        provider=None,
        provider_payment_id=None,
        reference=None,
        submitted_at=None,
        completed_at=None,
        failure_reason=None,
        correlation_id=uuid.uuid4(),
        # International fields (migration 0017) — None on domestic
        # same-currency payments, populated by the orchestrator on
        # cross-border ones. See `services/international_payments.py`.
        source_currency=None,
        source_amount=None,
        fx_rate=None,
        fx_locked_at=None,
        corridor=None,
        target_country=None,
    )


def _invoice(*, status=InvoiceStatus.approved, vendor_id=None, organization_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        status=status,
        invoice_number="INV-1",
        vendor_name="Acme Corp",
        # A payable invoice always has a vendor — the executor now holds any
        # invoice with no screenable vendor (NULL vendor_id was a sanctions
        # bypass), so a default-None invoice would never reach the adapter.
        vendor_id=vendor_id if vendor_id is not None else uuid.uuid4(),
        currency="USD",
        description=None,
        amount=Decimal("100.00"),
    )


def _mock_db(*, run, payments, invoice_by_id):
    """Build a DB session whose execute() returns the right shape for
    each query the executor issues:
      1. `select(PaymentRun).where(PaymentRun.id == run_id)` → scalar
      2. `select(Payment).where(Payment.payment_run_id == run_id)` →
         scalars().all()
      3. Per payment: `select(Invoice).where(Invoice.id == ...)` →
         scalar. If the invoice has `vendor_id` set, the executor also
         runs `select(Vendor.bank_details).where(...)`; enqueue a None
         bank result only in that case.
    """
    run_result = MagicMock()
    run_result.scalar_one_or_none = MagicMock(return_value=run)

    payments_result = MagicMock()
    payments_scalars = MagicMock()
    payments_scalars.all = MagicMock(return_value=payments)
    payments_result.scalars = MagicMock(return_value=payments_scalars)

    per_pay_results: list = []
    for p in payments:
        inv = invoice_by_id.get(str(p.invoice_id))
        inv_res = MagicMock()
        inv_res.scalar_one_or_none = MagicMock(return_value=inv)
        per_pay_results.append(inv_res)
        if inv is not None and getattr(inv, "vendor_id", None):
            # bank_details SELECT (payload / intl detection) → None (domestic).
            bank_res = MagicMock()
            bank_res.scalar_one_or_none = MagicMock(return_value=None)
            per_pay_results.append(bank_res)
            # compliance-gate Vendor SELECT → a vendor row (screened clear by
            # the autouse check_payment_compliance patch below).
            ven_res = MagicMock()
            ven_res.scalar_one_or_none = MagicMock(
                return_value=SimpleNamespace(id=inv.vendor_id, name="Acme Corp")
            )
            per_pay_results.append(ven_res)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[run_result, payments_result, *per_pay_results])
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


def _org():
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug="acme",
        settings={"payments": {"provider": "mock"}},
    )


def _user():
    return SimpleNamespace(id=uuid.uuid4(), full_name="Tester", roles=["admin"])


def _adapter_returning(*statuses: PaymentStatus, failure_reason: str | None = None):
    """Build a mock adapter whose create_payment cycles through the
    given PaymentStatus values, one per call."""
    adapter = MagicMock()
    adapter.provider_name = "mock"
    in_flight_statuses = (PaymentStatus.submitted, PaymentStatus.processing)
    results = [
        SimpleNamespace(
            success=(s == PaymentStatus.completed or s in in_flight_statuses),
            status=s,
            provider_payment_id=f"px_{i}",
            reference=f"REF-{i}",
            failure_reason=failure_reason if s == PaymentStatus.failed else None,
        )
        for i, s in enumerate(statuses)
    ]
    adapter.create_payment = AsyncMock(side_effect=results)
    return adapter


# ---------------------------------------------------------------------------
# Run-status rollup — the four outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_rolls_up_to_completed_when_all_payments_settle_synchronously():
    """Mock adapter returns PaymentStatus.completed for every row.
    Run.status must become `completed`. ERP-sync must fire exactly
    once (since at least one payment newly completed)."""
    from app.api.payments import execute_payment_run

    run = _run()
    p1, p2 = _payment(), _payment()
    invoices = {str(p1.invoice_id): _invoice(), str(p2.invoice_id): _invoice()}
    db = _mock_db(run=run, payments=[p1, p2], invoice_by_id=invoices)

    with (
        patch(
            "app.api.payments.get_payment_adapter",
            return_value=_adapter_returning(PaymentStatus.completed, PaymentStatus.completed),
        ),
        patch("app.services.payment_erp_sync.dispatch_payment_sync", AsyncMock()) as mk_sync,
    ):
        result = await execute_payment_run(run_id=run.id, db=db, org=_org(), user=_user())

    assert run.status == "completed"
    assert run.executed_at is not None
    assert result["payments_completed"] == 2
    assert result["payments_failed"] == 0
    assert result["payments_in_flight"] == 0
    assert p1.status == "completed"
    assert p2.status == "completed"
    # Both invoices flipped to payment_scheduled.
    assert all(inv.status == InvoiceStatus.payment_scheduled for inv in invoices.values())
    mk_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_rolls_up_to_failed_when_every_payment_fails():
    """All adapter calls fail → run.status = failed; ERP sync does
    NOT fire (no newly-completed payments)."""
    from app.api.payments import execute_payment_run

    run = _run()
    p1, p2 = _payment(), _payment()
    invoices = {str(p1.invoice_id): _invoice(), str(p2.invoice_id): _invoice()}
    db = _mock_db(run=run, payments=[p1, p2], invoice_by_id=invoices)

    with (
        patch(
            "app.api.payments.get_payment_adapter",
            return_value=_adapter_returning(
                PaymentStatus.failed, PaymentStatus.failed, failure_reason="insufficient_funds"
            ),
        ),
        patch("app.services.payment_erp_sync.dispatch_payment_sync", AsyncMock()) as mk_sync,
    ):
        result = await execute_payment_run(run_id=run.id, db=db, org=_org(), user=_user())

    assert run.status == "failed"
    assert result["payments_failed"] == 2
    assert result["payments_completed"] == 0
    assert p1.status == "failed"
    assert p1.failure_reason == "insufficient_funds"
    # Invoices stayed at approved — no scheduling on a failed run.
    assert all(inv.status == InvoiceStatus.approved for inv in invoices.values())
    mk_sync.assert_not_called()


@pytest.mark.asyncio
async def test_run_rolls_up_to_partial_on_mixed_outcomes():
    """One completed + one failed → run.status = partial. ERP sync
    fires because at least one payment newly completed."""
    from app.api.payments import execute_payment_run

    run = _run()
    p1, p2 = _payment(), _payment()
    invoices = {str(p1.invoice_id): _invoice(), str(p2.invoice_id): _invoice()}
    db = _mock_db(run=run, payments=[p1, p2], invoice_by_id=invoices)

    with (
        patch(
            "app.api.payments.get_payment_adapter",
            return_value=_adapter_returning(PaymentStatus.completed, PaymentStatus.failed),
        ),
        patch("app.services.payment_erp_sync.dispatch_payment_sync", AsyncMock()) as mk_sync,
    ):
        result = await execute_payment_run(run_id=run.id, db=db, org=_org(), user=_user())

    assert run.status == "partial"
    assert result["payments_completed"] == 1
    assert result["payments_failed"] == 1
    # First invoice scheduled, second still approved.
    assert invoices[str(p1.invoice_id)].status == InvoiceStatus.payment_scheduled
    assert invoices[str(p2.invoice_id)].status == InvoiceStatus.approved
    mk_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_rolls_up_to_submitted_when_payments_are_in_flight():
    """Adapter returns PaymentStatus.submitted / .processing → run
    status `submitted`. ERP sync does NOT fire — webhook will finalize
    + sync once the rail confirms."""
    from app.api.payments import execute_payment_run

    run = _run()
    p1, p2 = _payment(), _payment()
    invoices = {str(p1.invoice_id): _invoice(), str(p2.invoice_id): _invoice()}
    db = _mock_db(run=run, payments=[p1, p2], invoice_by_id=invoices)

    with (
        patch(
            "app.api.payments.get_payment_adapter",
            return_value=_adapter_returning(PaymentStatus.submitted, PaymentStatus.processing),
        ),
        patch("app.services.payment_erp_sync.dispatch_payment_sync", AsyncMock()) as mk_sync,
    ):
        result = await execute_payment_run(run_id=run.id, db=db, org=_org(), user=_user())

    assert run.status == "submitted"
    assert result["payments_in_flight"] == 2
    assert p1.status == "submitted"
    assert p2.status == "processing"
    # In-flight payments still schedule the invoice — money is
    # committed even before settlement.
    assert all(inv.status == InvoiceStatus.payment_scheduled for inv in invoices.values())
    mk_sync.assert_not_called()


# ---------------------------------------------------------------------------
# Per-payment field hydration from the adapter result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_fields_hydrate_from_adapter_result():
    """The executor must stamp `provider`, `provider_payment_id`, and
    `reference` from the adapter response, plus `submitted_at` /
    `completed_at` from the wall clock. A regression that loses any
    of these breaks webhook correlation (provider_payment_id is the
    key the webhook handler joins on)."""
    from app.api.payments import execute_payment_run

    run = _run()
    p = _payment()
    invoices = {str(p.invoice_id): _invoice()}
    db = _mock_db(run=run, payments=[p], invoice_by_id=invoices)

    before = datetime.now(UTC)
    with (
        patch(
            "app.api.payments.get_payment_adapter",
            return_value=_adapter_returning(PaymentStatus.completed),
        ),
        patch("app.services.payment_erp_sync.dispatch_payment_sync", AsyncMock()),
    ):
        await execute_payment_run(run_id=run.id, db=db, org=_org(), user=_user())

    assert p.provider == "mock"
    assert p.provider_payment_id == "px_0"
    assert p.reference == "REF-0"
    assert p.submitted_at >= before
    assert p.completed_at >= before


# ---------------------------------------------------------------------------
# Idempotency guards — already covered by test_payment_run_actions.py,
# but the integration tests below confirm the guard fires before any
# adapter call is made.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_refuses_already_completed_run_without_calling_adapter():
    """A completed run re-executed must 409 *before* any adapter
    call. A regression that ran the adapter first and then errored
    on commit would double-charge."""
    from fastapi import HTTPException

    from app.api.payments import execute_payment_run

    run = _run(status="completed")
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=run)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    with (
        patch("app.api.payments.get_payment_adapter") as mk_adapter,
        patch("app.services.payment_erp_sync.dispatch_payment_sync", AsyncMock()) as mk_sync,
    ):
        with pytest.raises(HTTPException) as exc:
            await execute_payment_run(run_id=run.id, db=db, org=_org(), user=_user())

    assert exc.value.status_code == 409
    mk_adapter.assert_not_called()
    mk_sync.assert_not_called()


@pytest.mark.asyncio
async def test_executor_refuses_cfo_unapproved_run_without_calling_adapter():
    """A run marked `requires_cfo_approval=True` with
    `cfo_approved_at=None` must 403 *before* the adapter is called.
    Otherwise the CFO gate is cosmetic — money has already moved."""
    from fastapi import HTTPException

    from app.api.payments import execute_payment_run

    run = _run(status="draft", requires_cfo=True, cfo_at=None)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=run)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    with (
        patch("app.api.payments.get_payment_adapter") as mk_adapter,
        patch("app.services.payment_erp_sync.dispatch_payment_sync", AsyncMock()) as mk_sync,
    ):
        with pytest.raises(HTTPException) as exc:
            await execute_payment_run(run_id=run.id, db=db, org=_org(), user=_user())

    assert exc.value.status_code == 403
    mk_adapter.assert_not_called()
    mk_sync.assert_not_called()
