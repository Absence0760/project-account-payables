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

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun
from app.models.workflow import AuditLog
from app.services.payment_erp_sync import _sync_payments


async def _set_org_erp(realdb, key: str, erp_config: dict | None) -> None:
    from app.config import settings as cfg
    from app.models.organization import Organization

    engine = create_async_engine(cfg.database_url)
    mk = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with mk() as s:
            org = (
                await s.execute(
                    select(Organization).where(Organization.id == realdb.info(key).org_id)
                )
            ).scalar_one()
            new_settings = dict(org.settings or {})
            if erp_config is None:
                new_settings.pop("erp", None)
            else:
                new_settings["erp"] = erp_config
            org.settings = new_settings
            await s.commit()
    finally:
        await engine.dispose()


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
