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

  BUG D — ``cancel_payment_run`` racing ``execute_payment_run``. Cancel
          read the run with a PLAIN SELECT while every sibling
          money-control endpoint locks FOR UPDATE — and cancel is the
          one that DELETES the child Payment rows. /execute locks,
          flips the run to `executing` and commits (releasing the lock)
          before its adapter loop, so a /cancel that read `draft`
          beforehand was never blocked and went on to delete the very
          payments being handed to the processor. Observed both ways
          against the unfixed code: the canceller winning outright
          (payment deleted, adapter never called, run reports success
          with nothing paid) and the payment vanishing mid-dispatch.
          The fix locks the run in cancel too, so the canceller blocks,
          re-reads `executing`, and 409s before deleting anything.
          Assertion: adapter called exactly once, the payment row
          survives carrying the processor's result, and the run is not
          `cancelled`.

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
from sqlalchemy import func, select, text

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


async def _wait_for_lock_waiter(session_mk, *, timeout: float = 15.0) -> bool:
    """Block until Postgres reports another backend in this database waiting on
    a lock, then return True (False if `timeout` elapses first).

    Asked of the server's own wait state — `pg_stat_activity.wait_event_type`,
    from an independent session — rather than slept for, so a test that needs
    "the second racer is now queued behind the first" waits on the real signal
    instead of hoping a fixed delay was long enough. Connecting as `postgres`
    (dev and CI both do) makes every backend's wait state visible.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        async with session_mk() as s:
            waiting = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() "
                        "AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
        if waiting:
            return True
        await asyncio.sleep(0.05)
    return False


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
                    entity_id=None,
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
                    entity_id=None,
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
                    entity_id=None,
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


# ---------------------------------------------------------------------------
# BUG D — cancel racing execute must not delete payments that are being paid
# ---------------------------------------------------------------------------


async def test_cancel_racing_execute_cannot_delete_dispatched_payments(realdb):
    """`/runs/{id}/cancel` read the run with a plain SELECT while every sibling
    money-control endpoint (/approve, /execute, /resume, /void) locks FOR
    UPDATE — and cancel is the one that DELETES the child Payment rows.

    The window: /execute takes the lock, re-checks `draft`, flips the run to
    `executing` and commits (releasing the lock) before its adapter loop. A
    /cancel that read `draft` before that commit was never blocked, so it
    proceeded to delete the very payments being handed to the processor. Real
    money moves, the Payment rows are gone, and the run reads `cancelled` — an
    outgoing payment with no record of itself.

    With the lock, the canceller blocks until /execute commits, re-reads
    `executing`, and 409s before deleting anything. Asserted on the durable
    outcome, not on timing: the adapter is still called exactly once AND the
    payment row survives with the processor's result on it.

    Unlike BUG A/B/C — two calls to the SAME endpoint, where either racer
    winning proves the point — the two racers here are different endpoints and
    only one ordering exercises the fix. So the ordering is pinned rather than
    left to the scheduler: /execute is held inside its locked transaction
    (`_gated_get_scoped_run`) until Postgres itself reports the canceller
    queued behind the lock.
    """
    from app.api import payments as payments_api
    from app.api.payments import cancel_payment_run, execute_payment_run

    info = realdb.info("a")
    org_id = info.org_id
    admin_id = info.users["admin"]
    creator_id = info.users["ap_manager"]  # maker-checker: creator ≠ executor
    mk = realdb.sessionmaker("a")

    inv = await _seed_invoice(mk, org_id, amount=Decimal("900.00"))

    run_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            PaymentRun(
                id=run_id,
                organization_id=org_id,
                status="draft",
                total_amount=Decimal("900.00"),
                initiated_by=creator_id,
                requires_cfo_approval=False,
            )
        )
        await s.flush()
        s.add(
            Payment(
                id=payment_id,
                invoice_id=inv.id,
                payment_run_id=run_id,
                amount=Decimal("900.00"),
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
        # Yield so the canceller coroutine gets to run mid-dispatch — the
        # exact interleaving the unlocked read allowed.
        await asyncio.sleep(0)
        return SimpleNamespace(
            success=True,
            status=PaymentStatus.completed,
            provider_payment_id="px_race",
            reference="REF-race",
            failure_reason=None,
        )

    adapter = SimpleNamespace(provider_name="mock", create_payment=_counting_create_payment)

    lock_held = asyncio.Event()  # /execute now holds the run row's FOR UPDATE lock
    release_execute = asyncio.Event()  # …and the canceller is queued behind it
    real_get_scoped_run = payments_api._get_scoped_run
    gated = False

    async def _gated_get_scoped_run(*args, **kwargs):
        """Pin the interleaving this test exists for: the canceller arriving
        while /execute already holds the run's row lock.

        Firing both coroutines at `asyncio.gather` does not order them — it
        only schedules them, and whichever session reaches `SELECT ... FOR
        UPDATE` first wins. Both outcomes are correct (the loser 409s either
        way), so an assertion written for one arm fails outright when the
        scheduler picks the other — which is how this test went red on CI with
        `create_payment called 0x`. Worse, the canceller-wins arm exercises
        nothing: the UNFIXED cancel produces exactly the same result, because
        it reaches the still-`draft` run before /execute claims it. Holding
        /execute inside its locked transaction until the canceller has queued
        behind the lock makes the race deterministic AND keeps it a real guard
        — an unlocked cancel reads `draft` right through this window and
        deletes the payments being dispatched.
        """
        nonlocal gated
        run_row = await real_get_scoped_run(*args, **kwargs)
        if not gated:  # the first caller through is /execute
            gated = True
            lock_held.set()
            await release_execute.wait()
        return run_row

    async def _execute_once():
        async with realdb.sessionmaker("a")() as db:
            try:
                return (
                    "ok",
                    await execute_payment_run(
                        run_id=run_id,
                        db=db,
                        org=_org(org_id),
                        user=_user(admin_id),
                        entity_id=None,
                    ),
                )
            except HTTPException as exc:
                await db.rollback()
                return ("http", exc.status_code)

    async def _cancel_once():
        async with realdb.sessionmaker("a")() as db:
            try:
                return (
                    "ok",
                    await cancel_payment_run(
                        run_id=run_id,
                        db=db,
                        org=_org(org_id),
                        user=_user(admin_id),
                        entity_id=None,
                    ),
                )
            except HTTPException as exc:
                await db.rollback()
                return ("http", exc.status_code)

    with (
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.api.payments._get_scoped_run", new=_gated_get_scoped_run),
        patch("app.api.payments.transition_invoice", new_callable=AsyncMock) as ti,
        patch("app.services.payment_erp_sync.dispatch_payment_sync", new_callable=AsyncMock),
        patch(
            "app.services.compliance.check_payment_compliance",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(verdict="allow", reasons=[]),
        ),
    ):
        ti.return_value = inv
        exec_task = asyncio.create_task(_execute_once())
        await asyncio.wait_for(lock_held.wait(), timeout=15)
        cancel_task = asyncio.create_task(_cancel_once())
        # Postgres' own wait state is the signal that the canceller is
        # contending for the run row — poll it, never sleep a guess.
        canceller_queued = await _wait_for_lock_waiter(realdb.sessionmaker("a"))
        release_execute.set()
        results = await asyncio.gather(exec_task, cancel_task)

    assert canceller_queued, (
        "the canceller never queued behind /execute's row lock — it read the "
        "run unlocked, which is the race this test guards"
    )
    assert call_count == 1, f"adapter.create_payment called {call_count}x"
    # The canceller must have lost — it cannot cancel a run already executing.
    assert ("http", 409) in results, f"expected the canceller to 409, got {results}"

    async with mk() as s:
        pay = (
            await s.execute(select(Payment).where(Payment.id == payment_id))
        ).scalar_one_or_none()
        assert pay is not None, (
            "the dispatched payment row was deleted out from under the processor"
        )
        assert pay.status == "completed"
        assert pay.provider_payment_id == "px_race"
        run = (await s.execute(select(PaymentRun).where(PaymentRun.id == run_id))).scalar_one()
        assert run.status != "cancelled"
