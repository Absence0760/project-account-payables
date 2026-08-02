"""Real-Postgres concurrency tests for the money-moving payment path.

These prove the row-lock + atomic-flip fixes for two confirmed race
bugs that the mock-session suite structurally cannot catch (a single
MagicMock session can't model two connections contending for a row
lock). They use the ``realdb`` fixture's per-key session makers — each
``sessionmaker(...)`` call hands back an independent engine/connection,
so two coroutines run against genuinely separate DB sessions and the
Postgres ``SELECT ... FOR UPDATE`` actually serializes them.

  BUG A — concurrent ``execute_payment_run`` double-pays. Two /execute
          calls used to both read ``status == "draft"`` and both loop
          through ``adapter.create_payment`` → the processor charged
          twice for the same payment rows. The fix locks the run row
          FOR UPDATE, re-checks ``draft`` inside the transaction, and
          atomically flips it to ``executing`` (committing so the lock
          releases) before the adapter loop — so the loser sees a
          non-draft run and 409s. Assertion: the adapter's
          ``create_payment`` is awaited *exactly once* across both
          racers, and exactly one racer 409s.

  BUG B — concurrent ``void_payment`` double-voids. Two /void calls used
          to both pass the ``status in (voided, cancelled)`` guard, both
          call the adapter ``void_payment``, and both write a
          ``payment.voided`` audit row. The fix locks the payment row
          FOR UPDATE and re-checks status inside the transaction.
          Assertion: the adapter's ``void_payment`` is awaited exactly
          once and exactly one ``payment.voided`` audit row is written.

  BUG C — concurrent ``resume_payment_run`` double-dispatches. Unlike
          /execute, /resume never re-claims the run row before the
          adapter loop (the run is already `executing`, so there's
          nothing to atomically flip) — ``_dispatch_run_payments``'
          pending-payment query was a plain, unlocked SELECT. Two
          concurrent /resume calls both loaded the same still-`pending`
          payment and both dispatched it to the adapter. The fix
          re-locks and re-checks each payment (``db.refresh(...,
          with_for_update=True)``) immediately before dispatch, mirroring
          the reconciler's claim pattern — the loser sees the row already
          claimed (no longer `pending`) and skips it. Assertion: the
          adapter's ``create_payment`` is awaited exactly once across
          both racers, and both calls return 200 (resume has no run-lock
          loser to 409 — the race is per-payment, not per-run).

Requires the dev Postgres (``pnpm db:up``); skips otherwise, like every
other ``realdb`` test.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun
from app.models.workflow import AuditLog
from app.services.payment_adapters import PaymentStatus

pytestmark = pytest.mark.asyncio


def _user(uid: uuid.UUID, role: str = "admin"):
    return SimpleNamespace(id=uid, full_name="Concurrency Tester", roles=[role])


def _org(org_id: uuid.UUID, *, provider: str = "mock"):
    return SimpleNamespace(
        id=org_id,
        name="PyTest",
        slug="pytesta",
        settings={"payments": {"provider": provider}},
    )


async def _seed_invoice(session_mk, org_id: uuid.UUID, *, amount: Decimal) -> SimpleNamespace:
    """Insert an approved invoice (with a real vendor) and return a detached
    snapshot (id + correlation_id) so callers never lazy-load against a closed
    NullPool connection. The vendor is required because the executor now holds
    any invoice with no screenable vendor (NULL vendor_id was a sanctions
    bypass) — without one the payment would never reach the adapter."""
    from app.models.vendor import Vendor

    inv_id = uuid.uuid4()
    vendor_id = uuid.uuid4()
    corr = uuid.uuid4()
    async with session_mk() as s:
        s.add(Vendor(id=vendor_id, name="Acme Corp", organization_id=org_id))
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
                vendor_name="Acme Corp",
                vendor_id=vendor_id,
                amount=amount,
                currency="USD",
                status=InvoiceStatus.approved,
                organization_id=org_id,
                correlation_id=corr,
            )
        )
        await s.commit()
    return SimpleNamespace(id=inv_id, correlation_id=corr, vendor_id=vendor_id)


# ---------------------------------------------------------------------------
# BUG A — concurrent execute_payment_run must not double-pay
# ---------------------------------------------------------------------------


async def test_concurrent_execute_run_charges_adapter_exactly_once(realdb):
    """Two concurrent /execute calls on the same draft run must result in
    the payment adapter being called exactly once — money cannot move
    twice for the same rows. The loser of the row-lock race gets a 409.
    """
    from app.api.payments import execute_payment_run

    info = realdb.info("a")
    org_id = info.org_id
    admin_id = info.users["admin"]
    # Maker-checker: the run's creator must differ from the executor (admin),
    # else the segregation control 403s both racers before the adapter is ever
    # reached. The creator here is a different real user; this test is about the
    # FOR UPDATE double-execute race, not SoD.
    creator_id = info.users["ap_manager"]
    mk = realdb.sessionmaker("a")

    inv = await _seed_invoice(mk, org_id, amount=Decimal("100.00"))

    # Create a draft run + one pending payment.
    run_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            PaymentRun(
                id=run_id,
                organization_id=org_id,
                status="draft",
                total_amount=Decimal("100.00"),
                initiated_by=creator_id,
                requires_cfo_approval=False,
            )
        )
        await s.flush()
        s.add(
            Payment(
                id=uuid.uuid4(),
                invoice_id=inv.id,
                payment_run_id=run_id,
                amount=Decimal("100.00"),
                method="ach",
                status="pending",
                correlation_id=inv.correlation_id,
            )
        )
        await s.commit()

    # Shared adapter whose create_payment counts how many times the
    # processor was actually invoked across BOTH racers. A small await
    # yields control so both coroutines are in flight before either
    # finishes — the row lock, not timing, is what serializes them.
    call_count = 0

    async def _counting_create_payment(payload):
        nonlocal call_count
        call_count += 1
        # Let the event loop run the sibling coroutine before returning.
        await asyncio.sleep(0)
        return SimpleNamespace(
            success=True,
            status=PaymentStatus.completed,
            provider_payment_id=f"px_{call_count}",
            reference=f"REF-{call_count}",
            failure_reason=None,
        )

    adapter = SimpleNamespace(
        provider_name="mock",
        create_payment=_counting_create_payment,
    )

    async def _run_once():
        session_mk = realdb.sessionmaker("a")
        async with session_mk() as db:
            try:
                res = await execute_payment_run(
                    run_id=run_id,
                    db=db,
                    org=_org(org_id),
                    user=_user(admin_id),
                )
                await db.commit()
                return ("ok", res)
            except HTTPException as exc:
                await db.rollback()
                return ("http", exc.status_code)

    with (
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.api.payments.transition_invoice", new_callable=AsyncMock) as ti,
        patch("app.services.payment_erp_sync.dispatch_payment_sync", new_callable=AsyncMock),
        # This test is about the FOR UPDATE double-execute race, not the
        # sanctions gate — clear it so the (vendored) payment reaches the adapter.
        patch(
            "app.services.compliance.check_payment_compliance",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(verdict="allow", reasons=[]),
        ),
    ):
        ti.return_value = inv
        results = await asyncio.gather(_run_once(), _run_once())

    # Exactly one racer executed; the other 409'd on the non-draft run.
    statuses = [r for kind, r in results if kind == "http"]
    oks = [r for kind, r in results if kind == "ok"]
    assert call_count == 1, f"adapter.create_payment called {call_count}x (double-pay!)"
    assert statuses == [409], f"expected one 409 loser, got {results}"
    assert len(oks) == 1

    # And the run + its payment landed in a single completed state.
    async with mk() as s:
        run = (await s.execute(select(PaymentRun).where(PaymentRun.id == run_id))).scalar_one()
        assert run.status == "completed"
        pays = (
            (await s.execute(select(Payment).where(Payment.payment_run_id == run_id)))
            .scalars()
            .all()
        )
        assert len(pays) == 1
        assert pays[0].status == "completed"


# ---------------------------------------------------------------------------
# BUG C — concurrent resume_payment_run must not double-dispatch
# ---------------------------------------------------------------------------


async def test_concurrent_resume_charges_adapter_exactly_once(realdb):
    """Two concurrent /resume calls on the same stuck-`executing` run must
    result in the payment adapter being called exactly once — mirrors BUG A
    but for the crash-recovery path, which has no run-level claim to lock
    (the run is already `executing`) so the race is per-payment instead.
    """
    from app.api.payments import resume_payment_run

    info = realdb.info("a")
    org_id = info.org_id
    admin_id = info.users["admin"]
    creator_id = info.users["ap_manager"]
    mk = realdb.sessionmaker("a")

    inv = await _seed_invoice(mk, org_id, amount=Decimal("100.00"))

    # A run stuck `executing` with one still-`pending` payment — exactly the
    # state a crashed execute_payment_run worker leaves behind.
    run_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            PaymentRun(
                id=run_id,
                organization_id=org_id,
                status="executing",
                total_amount=Decimal("100.00"),
                initiated_by=creator_id,
                requires_cfo_approval=False,
            )
        )
        await s.flush()
        s.add(
            Payment(
                id=uuid.uuid4(),
                invoice_id=inv.id,
                payment_run_id=run_id,
                amount=Decimal("100.00"),
                method="ach",
                status="pending",
                correlation_id=inv.correlation_id,
            )
        )
        await s.commit()

    call_count = 0

    async def _counting_create_payment(payload):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)
        return SimpleNamespace(
            success=True,
            status=PaymentStatus.completed,
            provider_payment_id=f"px_{call_count}",
            reference=f"REF-{call_count}",
            failure_reason=None,
        )

    adapter = SimpleNamespace(
        provider_name="mock",
        create_payment=_counting_create_payment,
    )

    async def _resume_once():
        session_mk = realdb.sessionmaker("a")
        async with session_mk() as db:
            try:
                res = await resume_payment_run(
                    run_id=run_id,
                    db=db,
                    org=_org(org_id),
                    user=_user(admin_id),
                )
                await db.commit()
                return ("ok", res)
            except HTTPException as exc:
                await db.rollback()
                return ("http", exc.status_code)

    with (
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.api.payments.transition_invoice", new_callable=AsyncMock) as ti,
        patch("app.services.payment_erp_sync.dispatch_payment_sync", new_callable=AsyncMock),
        patch(
            "app.services.compliance.check_payment_compliance",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(verdict="allow", reasons=[]),
        ),
    ):
        ti.return_value = inv
        results = await asyncio.gather(_resume_once(), _resume_once())

    # Both /resume calls return 200 (no run-lock loser here — the race is
    # per-payment) but the adapter was only ever charged once.
    assert call_count == 1, f"adapter.create_payment called {call_count}x (double-dispatch!)"
    assert all(kind == "ok" for kind, _ in results), f"expected both to succeed, got {results}"

    async with mk() as s:
        run = (await s.execute(select(PaymentRun).where(PaymentRun.id == run_id))).scalar_one()
        assert run.status == "completed"
        pays = (
            (await s.execute(select(Payment).where(Payment.payment_run_id == run_id)))
            .scalars()
            .all()
        )
        assert len(pays) == 1
        assert pays[0].status == "completed"


# ---------------------------------------------------------------------------
# BUG B — concurrent void_payment must not double-void
# ---------------------------------------------------------------------------


async def test_concurrent_void_calls_adapter_and_audits_exactly_once(realdb):
    """Two concurrent /void calls on the same completed payment must call
    the adapter's void_payment exactly once and write exactly one
    `payment.voided` audit row. The loser of the row-lock race gets 409.
    """
    from app.api.payments import VoidPaymentRequest, void_payment

    info = realdb.info("a")
    org_id = info.org_id
    admin_id = info.users["admin"]
    mk = realdb.sessionmaker("a")

    inv = await _seed_invoice(mk, org_id, amount=Decimal("250.00"))

    payment_id = uuid.uuid4()
    corr = uuid.uuid4()
    async with mk() as s:
        s.add(
            Payment(
                id=payment_id,
                invoice_id=inv.id,
                payment_run_id=None,
                amount=Decimal("250.00"),
                method="ach",
                status="completed",
                provider="mock",
                provider_payment_id="px_live_1",
                completed_at=datetime.now(UTC),
                correlation_id=corr,
            )
        )
        await s.commit()

    void_count = 0

    async def _counting_void(provider_payment_id):
        nonlocal void_count
        void_count += 1
        await asyncio.sleep(0)
        return True

    adapter = SimpleNamespace(
        provider_name="mock",
        void_payment=_counting_void,
    )

    async def _void_once():
        session_mk = realdb.sessionmaker("a")
        async with session_mk() as db:
            try:
                res = await void_payment(
                    payment_id=payment_id,
                    body=VoidPaymentRequest(reason="duplicate"),
                    db=db,
                    org=_org(org_id),
                    user=_user(admin_id),
                )
                return ("ok", res)
            except HTTPException as exc:
                await db.rollback()
                return ("http", exc.status_code)

    with (
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.api.payments.transition_invoice", new_callable=AsyncMock) as ti,
    ):
        ti.return_value = inv
        results = await asyncio.gather(_void_once(), _void_once())

    statuses = [r for kind, r in results if kind == "http"]
    assert void_count == 1, f"adapter.void_payment called {void_count}x (double-void!)"
    assert statuses == [409], f"expected one 409 loser, got {results}"

    # Exactly one payment.voided audit row exists for this payment.
    async with mk() as s:
        audit_count = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "payment.voided",
                    AuditLog.entity_id == payment_id,
                )
            )
        ).scalar()
        assert audit_count == 1, f"expected 1 payment.voided audit row, got {audit_count}"

        pay = (await s.execute(select(Payment).where(Payment.id == payment_id))).scalar_one()
        assert pay.status == "voided"
