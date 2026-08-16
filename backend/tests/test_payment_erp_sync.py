"""Coverage for ``app.services.payment_erp_sync`` — the post-payment-run
sync-back that marks ``payment_scheduled`` invoices ``paid`` and writes an
audit row.

``_sync_payments`` builds its own control + tenant engines from
``settings.database_url`` / ``org.db_name``, so the realdb test tenants
(which live in the same Postgres) are reachable from inside it. The tenant
data is committed before the call, and re-read with a fresh session after.
``settings.erp`` is set on the control-plane org so the sync isn't skipped.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun
from app.models.workflow import AuditLog
from app.services import payment_erp_sync
from app.services.payment_erp_sync import _sync_payments


@pytest.fixture(autouse=True)
def _redirect_database_url_to_slot(realdb, monkeypatch):
    """Point the global ``settings.database_url`` at THIS slot's own
    control-plane DB for the duration of each test.

    ``_sync_payments`` deliberately does NOT go through the shared
    `app.database.control_session_factory` — it builds its own throwaway
    control + tenant engines straight off `settings.database_url` /
    `org.db_name` every call, because in production it also runs detached
    from any request/test event loop (`dispatch_payment_sync` fires it on a
    background thread with a brand-new loop, so it can't safely reuse a
    pooled engine bound to a different loop). That means it can't pick up the
    per-slot control-plane DB the way code that reads
    `control_session_factory` does — the harness has to redirect the global
    `settings.database_url` itself instead. Safe because `settings` is a
    module-global singleton every reader shares, monkeypatch reverts it after
    the test, and `_make_tenant_url` (used for the tenant-side engine) only
    ever keeps the host/port/credentials from this value and replaces the
    trailing db-name segment — so the real tenant DBs stay reachable exactly
    as before.
    """
    monkeypatch.setattr(settings, "database_url", realdb.control_db_url())


async def _set_org_erp(realdb, key: str, erp_config: dict | None) -> None:
    from app.models.organization import Organization

    # Goes through realdb.control_sessionmaker() (not a bare
    # create_async_engine(cfg.database_url)) — the harness's org lives in this
    # process's per-slot control-plane database, not the real, shared one.
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == realdb.info(key).org_id))
        ).scalar_one()
        new_settings = dict(org.settings or {})
        if erp_config is None:
            new_settings.pop("erp", None)
        else:
            new_settings["erp"] = erp_config
        org.settings = new_settings
        await s.commit()


async def _seed_run(
    realdb,
    key: str,
    *,
    invoice_status: InvoiceStatus = InvoiceStatus.payment_scheduled,
    amount: Decimal = Decimal("100.00"),
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a payment run with one invoice + payment. Returns (run_id, invoice_id)."""
    mk = realdb.sessionmaker(key)
    org_id = realdb.info(key).org_id
    run_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    async with mk() as s:
        s.add(PaymentRun(id=run_id, status="completed", organization_id=org_id))
        s.add(
            Invoice(
                id=invoice_id,
                invoice_number="INV-SYNC-1",
                vendor_name="Vendor Co",
                amount=amount,
                status=invoice_status,
                organization_id=org_id,
            )
        )
        # Flush the run + invoice so the Payment's FKs resolve (no ORM
        # relationship orders these inserts).
        await s.flush()
        s.add(
            Payment(
                invoice_id=invoice_id,
                payment_run_id=run_id,
                amount=amount,
                method="ach",
                status="completed",
            )
        )
        await s.commit()
    return run_id, invoice_id


async def test_sync_marks_scheduled_invoice_paid(realdb):
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id = await _seed_run(realdb, "a")

    await _sync_payments(run_id, realdb.info("a").org_id)

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
    assert inv.status == InvoiceStatus.paid


async def test_sync_writes_audit_row(realdb):
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id = await _seed_run(realdb, "a")

    await _sync_payments(run_id, realdb.info("a").org_id)

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(AuditLog.action == "invoice.paid_via_erp_sync")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    audit = rows[0]
    assert audit.entity_type == "invoice"
    assert audit.entity_id == invoice_id
    assert audit.details["old_status"] == "payment_scheduled"
    assert audit.details["new_status"] == "paid"


async def test_sync_skips_when_no_erp_configured(realdb):
    # No settings.erp -> the sync returns early and the invoice stays scheduled.
    await _set_org_erp(realdb, "a", None)
    run_id, invoice_id = await _seed_run(realdb, "a")

    await _sync_payments(run_id, realdb.info("a").org_id)

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
        audit_count = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "invoice.paid_via_erp_sync")
            )
        ).scalar_one()
    assert inv.status == InvoiceStatus.payment_scheduled
    assert audit_count == 0


async def test_sync_missing_org_is_noop(realdb):
    # Unknown org id -> early return, no error.
    await _sync_payments(uuid.uuid4(), uuid.uuid4())  # should not raise


async def test_sync_only_transitions_scheduled_invoices(realdb):
    # An invoice already `paid` is left alone — no second transition / audit row.
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id = await _seed_run(realdb, "a", invoice_status=InvoiceStatus.paid)

    await _sync_payments(run_id, realdb.info("a").org_id)

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
        audit_count = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "invoice.paid_via_erp_sync")
            )
        ).scalar_one()
    assert inv.status == InvoiceStatus.paid
    assert audit_count == 0


# NOTE: _sync_payments LEFT-outer-joins Invoice defensively, but
# Payment.invoice_id is NOT NULL with a real FK to invoices, so a payment with
# no matching invoice cannot be persisted — that branch is unreachable through
# the real schema and isn't exercised here.


async def test_sync_does_not_pay_in_flight_payment_invoice(realdb):
    """A run can mix a settled `completed` payment with an in-flight
    `submitted` one (real money still moving, terminal status arrives via
    webhook). ERP sync fires once at least one payment settled, but it must
    mark `paid` ONLY the invoices whose own payment is `completed`. Flipping
    an in-flight payment's invoice to `paid` would claim money moved before
    the rail confirmed it — and would pre-empt the webhook's own `paid`
    transition. Regression for the mixed-run false-paid bug."""
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    run_id = uuid.uuid4()
    inv_completed = uuid.uuid4()
    inv_in_flight = uuid.uuid4()
    async with mk() as s:
        s.add(PaymentRun(id=run_id, status="submitted", organization_id=org_id))
        for iid, num in ((inv_completed, "INV-DONE"), (inv_in_flight, "INV-FLIGHT")):
            s.add(
                Invoice(
                    id=iid,
                    invoice_number=num,
                    vendor_name="V",
                    amount=Decimal("50.00"),
                    status=InvoiceStatus.payment_scheduled,
                    organization_id=org_id,
                )
            )
        await s.flush()
        s.add(
            Payment(
                invoice_id=inv_completed,
                payment_run_id=run_id,
                amount=Decimal("50.00"),
                method="ach",
                status="completed",
            )
        )
        s.add(
            Payment(
                invoice_id=inv_in_flight,
                payment_run_id=run_id,
                amount=Decimal("50.00"),
                method="ach",
                status="submitted",
            )
        )
        await s.commit()

    await _sync_payments(run_id, org_id)

    async with mk() as s:
        done = (await s.execute(select(Invoice).where(Invoice.id == inv_completed))).scalar_one()
        flight = (await s.execute(select(Invoice).where(Invoice.id == inv_in_flight))).scalar_one()
        # Exactly one paid-via-erp-sync audit row — for the completed payment only.
        audit_invoice_ids = (
            (
                await s.execute(
                    select(AuditLog.entity_id).where(AuditLog.action == "invoice.paid_via_erp_sync")
                )
            )
            .scalars()
            .all()
        )
    assert done.status == InvoiceStatus.paid
    assert flight.status == InvoiceStatus.payment_scheduled
    assert list(audit_invoice_ids) == [inv_completed]


async def test_sync_tenant_isolation(realdb):
    # A run that belongs to tenant "a" must not touch tenant "b"'s data.
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, _invoice_id = await _seed_run(realdb, "a")

    # Seed a same-status invoice under tenant "b" that is NOT in this run.
    mk_b = realdb.sessionmaker("b")
    org_b = realdb.info("b").org_id
    b_invoice_id = uuid.uuid4()
    async with mk_b() as s:
        s.add(
            Invoice(
                id=b_invoice_id,
                invoice_number="INV-B",
                vendor_name="B Vendor",
                amount=Decimal("10.00"),
                status=InvoiceStatus.payment_scheduled,
                organization_id=org_b,
            )
        )
        await s.commit()

    await _sync_payments(run_id, realdb.info("a").org_id)

    async with mk_b() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == b_invoice_id))).scalar_one()
    assert inv.status == InvoiceStatus.payment_scheduled


# ---------------------------------------------------------------------------
# PII-out-of-logs — a per-payment sync failure logs the exception CLASS only
# ---------------------------------------------------------------------------

_PII_SENTINEL = "SECRET_ACCOUNT_NUMBER_1234567890"


async def test_sync_logs_exception_class_not_message_on_failure(realdb, caplog):
    """A per-payment sync failure (e.g. `transition_invoice` raising) must log
    only the exception CLASS, never the raw message — an ERP/processor SDK
    error string can carry partial account data (PII-out-of-logs invariant).
    Regression for the bare `print(f"...: {exc}")` this module used to have."""
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, _invoice_id = await _seed_run(realdb, "a")

    with (
        patch(
            "app.services.workflow_engine.transition_invoice",
            AsyncMock(side_effect=RuntimeError(_PII_SENTINEL)),
        ),
        caplog.at_level(logging.WARNING, logger=payment_erp_sync.logger.name),
    ):
        await _sync_payments(run_id, realdb.info("a").org_id)

    assert caplog.records, "expected a WARNING log for the failed sync"
    for record in caplog.records:
        assert _PII_SENTINEL not in record.getMessage(), (
            f"PII leaked into log: {record.getMessage()}"
        )
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# A failed leg is a STRAND — it must be visible, and it must not take its
# siblings down with it.
#
# `_sync_payments` is the only path that flips `payment_scheduled → paid`, and
# nothing re-invokes it for a payment that is already `completed`. So a leg that
# raises leaves the money moved and the invoice never advancing — permanently.
# ---------------------------------------------------------------------------


async def _open_erp_reconciliation_rows(realdb, key: str, invoice_id: uuid.UUID) -> list:
    mk = realdb.sessionmaker(key)
    async with mk() as s:
        return list(
            (
                await s.execute(
                    select(APException).where(
                        APException.invoice_id == invoice_id,
                        APException.exception_type == "erp_reconciliation",
                        APException.status == "open",
                    )
                )
            )
            .scalars()
            .all()
        )


async def test_failed_leg_opens_an_erp_reconciliation_exception(realdb):
    """The money moved; the invoice didn't advance. Before this, the only trace
    was a WARNING line and a counter that died with the background thread — no
    row, no notification, nothing an AP manager could act on."""
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id = await _seed_run(realdb, "a")

    with patch(
        "app.services.workflow_engine.transition_invoice",
        AsyncMock(side_effect=RuntimeError("erp exploded")),
    ):
        result = await _sync_payments(run_id, realdb.info("a").org_id)

    assert result.failed == 1
    assert result.synced == 0

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
    assert inv.status == InvoiceStatus.payment_scheduled, "leg failed, so no false `paid`"

    rows = await _open_erp_reconciliation_rows(realdb, "a", invoice_id)
    assert len(rows) == 1, "the strand must be visible in the exception queue"
    exc_row = rows[0]
    assert exc_row.severity == "error"
    # PII-free + actionable: identifiers, the failure CLASS, and the exit.
    assert "RuntimeError" in exc_row.description
    assert "erp exploded" not in exc_row.description
    assert f"/api/payments/runs/{run_id}/sync-erp" in exc_row.description


async def test_unsupported_erp_adapter_strands_visibly_not_silently(realdb):
    """A typo in `settings.erp.type` is a leg failure, not a whole-run abort.

    `get_erp_adapter` fails closed on an ERP type it has no adapter for (the
    old `mock` fallback returned `success=True` with a fabricated document id
    — see `docs/decisions.md` §29). The refusal must be raised where every
    OTHER leg failure is raised, so it opens the same de-duped
    `erp_reconciliation` exception.

    Resolving it as a pre-flight before the tenant session instead would abort
    the run with a single count — and on the primary dispatch path
    (`dispatch_payment_sync` → `_run_in_thread`) that count is discarded, so
    every payment in the run would sit at `payment_scheduled` forever with no
    exception row, no notification, and no sweep to notice. That is precisely
    the invisibility this module was rewritten to remove.
    """
    await _set_org_erp(realdb, "a", {"type": "netsuite-oauth", "integration_method": "direct"})
    run_id, invoice_id = await _seed_run(realdb, "a")

    result = await _sync_payments(run_id, realdb.info("a").org_id)

    assert result.failed == 1
    assert result.synced == 0
    assert result.transitioned == 0

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
    assert inv.status == InvoiceStatus.payment_scheduled, "no false `paid` from a broken config"

    rows = await _open_erp_reconciliation_rows(realdb, "a", invoice_id)
    assert len(rows) == 1, "the strand must be visible in the exception queue"
    exc_row = rows[0]
    assert exc_row.severity == "error"
    assert "UnknownErpAdapterError" in exc_row.description
    assert f"/api/payments/runs/{run_id}/sync-erp" in exc_row.description


async def test_failed_leg_exception_is_deduped_across_retries(realdb):
    """A second failed pass must not pile up a second open row for the same
    invoice — the queue would fill with duplicates of one strand."""
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id = await _seed_run(realdb, "a")

    for _ in range(2):
        with patch(
            "app.services.workflow_engine.transition_invoice",
            AsyncMock(side_effect=RuntimeError("erp exploded")),
        ):
            await _sync_payments(run_id, realdb.info("a").org_id)

    rows = await _open_erp_reconciliation_rows(realdb, "a", invoice_id)
    assert len(rows) == 1


async def test_failed_leg_does_not_discard_a_sibling_leg_that_already_synced(realdb):
    """Regression: one failing leg used to roll back the WHOLE run.

    Every leg ran inside one transaction with a single commit at the end. A leg
    that failed with a real DB error poisoned that transaction, so the final
    commit raised, the outer handler rolled back — and the run's *successful*
    `payment_scheduled → paid` transitions were discarded too, silently, with
    nothing to re-invoke them. Legs are now committed independently.

    The payment ids are fixed so the SUCCEEDING leg is processed first (legs are
    ordered by `Payment.id`): that is the ordering the old code lost work in.
    """
    from sqlalchemy import text

    from app.services import workflow_engine

    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    run_id = uuid.uuid4()
    inv_ok = uuid.uuid4()
    inv_bad = uuid.uuid4()
    async with mk() as s:
        s.add(PaymentRun(id=run_id, status="completed", organization_id=org_id))
        for iid, num in ((inv_ok, "INV-OK"), (inv_bad, "INV-BAD")):
            s.add(
                Invoice(
                    id=iid,
                    invoice_number=num,
                    vendor_name="V",
                    amount=Decimal("40.00"),
                    status=InvoiceStatus.payment_scheduled,
                    organization_id=org_id,
                )
            )
        await s.flush()
        # uuid(int=1) sorts before uuid(int=2), so the good leg runs first.
        s.add(
            Payment(
                id=uuid.UUID(int=1),
                invoice_id=inv_ok,
                payment_run_id=run_id,
                amount=Decimal("40.00"),
                method="ach",
                status="completed",
            )
        )
        s.add(
            Payment(
                id=uuid.UUID(int=2),
                invoice_id=inv_bad,
                payment_run_id=run_id,
                amount=Decimal("40.00"),
                method="ach",
                status="completed",
            )
        )
        await s.commit()

    real_transition = workflow_engine.transition_invoice

    async def _explode_on_bad(db, invoice, *args, **kwargs):
        if invoice.id == inv_bad:
            # A genuine DB-level error — the shape that poisons the surrounding
            # transaction. A bare Python raise would not reproduce the bug.
            await db.execute(text("SELECT 1 / 0"))
        return await real_transition(db, invoice, *args, **kwargs)

    with patch("app.services.workflow_engine.transition_invoice", _explode_on_bad):
        result = await _sync_payments(run_id, org_id)

    assert result.synced == 1
    assert result.failed == 1

    async with mk() as s:
        ok = (await s.execute(select(Invoice).where(Invoice.id == inv_ok))).scalar_one()
        bad = (await s.execute(select(Invoice).where(Invoice.id == inv_bad))).scalar_one()
    assert ok.status == InvoiceStatus.paid, "a later failure must not undo an earlier success"
    assert bad.status == InvoiceStatus.payment_scheduled
    assert len(await _open_erp_reconciliation_rows(realdb, "a", inv_bad)) == 1
    assert await _open_erp_reconciliation_rows(realdb, "a", inv_ok) == []


# ---------------------------------------------------------------------------
# The exit — POST /api/payments/runs/{run_id}/sync-erp
# ---------------------------------------------------------------------------


async def test_retry_endpoint_recovers_a_stranded_invoice(realdb):
    """The strand's only legitimate exit. `/void` is not one — the money moved,
    and voiding returns the invoice to `approved` where it invites a second
    payment."""
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id = await _seed_run(realdb, "a")

    with patch(
        "app.services.workflow_engine.transition_invoice",
        AsyncMock(side_effect=RuntimeError("erp exploded")),
    ):
        await _sync_payments(run_id, realdb.info("a").org_id)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/payments/runs/{run_id}/sync-erp")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["synced"] == 1
    assert body["transitioned"] == 1, "the strand actually recovered"
    assert body["failed"] == 0

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
        actions = (await s.execute(select(AuditLog.action))).scalars().all()
    assert inv.status == InvoiceStatus.paid
    assert "payment_run.erp_sync_retried" in actions


async def test_retry_endpoint_is_idempotent(realdb):
    """A second call after a successful re-run must not write a second `paid`
    transition — the pass skips invoices already past `payment_scheduled`.

    Also pins the two counters apart: `synced` counts legs whose ERP-facing work
    completed (still 1 on the repeat, because the payment is still `completed`),
    while `transitioned` counts invoices this pass actually moved — the number
    that answers "did the retry recover anything", and the one that must read 0.
    """
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id = await _seed_run(realdb, "a")

    async with realdb.client(key="a", role="admin") as c:
        first = await c.post(f"/api/payments/runs/{run_id}/sync-erp")
        second = await c.post(f"/api/payments/runs/{run_id}/sync-erp")
    assert first.status_code == 200, first.text
    assert first.json()["synced"] == 1
    assert first.json()["transitioned"] == 1
    assert second.status_code == 200, second.text
    assert second.json()["synced"] == 1, "the leg still ran — the payment is still completed"
    assert second.json()["transitioned"] == 0, "but nothing advanced the second time"

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        paid_rows = (
            (
                await s.execute(
                    select(AuditLog).where(AuditLog.action == "invoice.paid_via_erp_sync")
                )
            )
            .scalars()
            .all()
        )
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
    assert inv.status == InvoiceStatus.paid
    assert len(paid_rows) == 1, "a repeat call must not write a second paid transition"


async def test_two_concurrent_passes_transition_the_invoice_once(realdb):
    """The retry endpoint awaits the pass synchronously, so a manual retry can
    overlap the background thread a webhook just dispatched for the same run.

    Without a row lock both passes read `payment_scheduled`, both clear the
    coverage check, and both call `transition_invoice` — a duplicate
    `invoice.paid_via_erp_sync` audit row and a duplicate "invoice paid"
    notification (which, unlike the outbound-webhook emit, carries no dedupe
    key). The invoice is taken FOR UPDATE, so the second pass re-reads it after
    the lock is granted, sees `paid`, and falls through.
    """
    import asyncio

    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id = await _seed_run(realdb, "a")
    org_id = realdb.info("a").org_id

    first, second = await asyncio.gather(
        _sync_payments(run_id, org_id),
        _sync_payments(run_id, org_id),
    )

    # Both legs ran (the payment is `completed` for both), but exactly one
    # of them moved the invoice.
    assert first.synced == 1 and second.synced == 1
    assert first.transitioned + second.transitioned == 1
    assert first.failed == 0 and second.failed == 0

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
        paid_rows = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "invoice.paid_via_erp_sync")
            )
        ).scalar_one()
    assert inv.status == InvoiceStatus.paid
    assert paid_rows == 1, "two concurrent passes must not both write the paid transition"


async def test_a_held_leg_releases_its_lock_and_its_siblings_still_sync(realdb):
    """A short-settled leg holds its invoice at `payment_scheduled` — but it
    took the row FOR UPDATE to decide that, so it must release before returning.

    Otherwise the lock lives until the session's next commit, i.e. for the rest
    of the run's legs, blocking any concurrent writer of a held invoice —
    including its own `POST /{id}/settlement/accept` release path, the very
    thing an operator reaches for next.
    """
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    run_id = uuid.uuid4()
    inv_short = uuid.uuid4()
    inv_full = uuid.uuid4()
    async with mk() as s:
        s.add(PaymentRun(id=run_id, status="completed", organization_id=org_id))
        for iid, num in ((inv_short, "INV-SHORT"), (inv_full, "INV-FULL")):
            s.add(
                Invoice(
                    id=iid,
                    invoice_number=num,
                    vendor_name="V",
                    amount=Decimal("500.00"),
                    currency="USD",
                    status=InvoiceStatus.payment_scheduled,
                    organization_id=org_id,
                )
            )
        await s.flush()
        # uuid(int=1) sorts first: the HELD leg runs before the syncing one, so
        # a lock it failed to release would still be held when the next leg runs.
        s.add(
            Payment(
                id=uuid.UUID(int=1),
                invoice_id=inv_short,
                payment_run_id=run_id,
                amount=Decimal("500.00"),
                method="ach",
                status="completed",
                settled_amount=Decimal("250.00"),
                settled_currency="USD",
            )
        )
        s.add(
            Payment(
                id=uuid.UUID(int=2),
                invoice_id=inv_full,
                payment_run_id=run_id,
                amount=Decimal("500.00"),
                method="ach",
                status="completed",
                settled_amount=Decimal("500.00"),
                settled_currency="USD",
            )
        )
        await s.commit()

    result = await _sync_payments(run_id, org_id)

    assert result.held == 1
    assert result.synced == 1
    assert result.transitioned == 1

    async with mk() as s:
        short = (await s.execute(select(Invoice).where(Invoice.id == inv_short))).scalar_one()
        full = (await s.execute(select(Invoice).where(Invoice.id == inv_full))).scalar_one()
    assert short.status == InvoiceStatus.payment_scheduled, "an under-settled invoice is held"
    assert full.status == InvoiceStatus.paid

    # The held invoice's row is free: a fresh session can take it FOR UPDATE
    # without waiting, which is what the accept/void release paths need.
    async with mk() as s:
        locked = (
            await s.execute(
                select(Invoice.id).where(Invoice.id == inv_short).with_for_update(nowait=True)
            )
        ).scalar_one()
        assert locked == inv_short
        await s.rollback()


async def test_retry_endpoint_409s_when_no_payment_settled(realdb):
    """Nothing settled means nothing to report to the ERP — refuse rather than
    silently reporting a zero-count success."""
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    run_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    async with mk() as s:
        s.add(PaymentRun(id=run_id, status="submitted", organization_id=org_id))
        s.add(
            Invoice(
                id=invoice_id,
                invoice_number="INV-FLIGHT-ONLY",
                vendor_name="V",
                amount=Decimal("12.00"),
                status=InvoiceStatus.payment_scheduled,
                organization_id=org_id,
            )
        )
        await s.flush()
        s.add(
            Payment(
                invoice_id=invoice_id,
                payment_run_id=run_id,
                amount=Decimal("12.00"),
                method="ach",
                status="submitted",
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/payments/runs/{run_id}/sync-erp")
    assert resp.status_code == 409, resp.text


async def test_retry_endpoint_404s_for_another_tenants_run(realdb):
    """Tenant isolation at the data layer: tenant "b"'s client must not be able
    to trigger a sync for tenant "a"'s run."""
    await _set_org_erp(realdb, "a", {"type": "mock", "integration_method": "direct"})
    run_id, _invoice_id = await _seed_run(realdb, "a")

    async with realdb.client(key="b", role="admin") as c:
        resp = await c.post(f"/api/payments/runs/{run_id}/sync-erp")
    assert resp.status_code == 404, resp.text
