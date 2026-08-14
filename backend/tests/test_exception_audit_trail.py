"""The AP exception lifecycle writes append-only ``audit_log`` rows.

An exception is a control, not a note: ``duplicate`` / ``fraud_flag`` /
``line_total_mismatch`` block a payment run, and invoice approval gates on none
of them — so clearing one is the human sign-off that lets money move. The
``exceptions`` table can't be the record of that (it is mutable and single-
valued, and it is not shipped to the SOC 2 WORM store), so every lifecycle event
goes through ``services/exception_lifecycle`` into ``audit_log``.

Covered here:
  * ``exception.raised`` on the shared create chokepoint, correlation-keyed to
    the invoice so it lands on that invoice's SOX trail;
  * an invoice-less exception (Positive Pay never-issued cheque) still audits,
    self-correlated;
  * the ``payment_blocking`` flag tracks the real payment-run gate;
  * ``details`` carries no generated description text.

Uses the real-Postgres harness so the rows are read back from the same tenant DB
the API writes to.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice
from app.models.workflow import AuditLog

TENANT = "a"


async def _make_invoice(mk, org_id, *, number="INV-EXCAUDIT-001"):
    inv = Invoice(
        id=uuid.uuid4(),
        organization_id=org_id,
        invoice_number=number,
        vendor_name="Globex Corporation",
        amount=Decimal("1234.56"),
        currency="USD",
        status="new",
    )
    async with mk() as s:
        s.add(inv)
        await s.commit()
    return inv


async def _audit_rows(mk, *, correlation_id=None, action=None):
    query = select(AuditLog)
    if correlation_id is not None:
        query = query.where(AuditLog.correlation_id == correlation_id)
    if action is not None:
        query = query.where(AuditLog.action == action)
    async with mk() as s:
        return (await s.execute(query.order_by(AuditLog.created_at))).scalars().all()


# ---------------------------------------------------------------------------
# exception.raised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_exception_writes_raised_audit_row_on_the_invoice_trail(realdb):
    from app.services.exception_service import create_exception

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv = await _make_invoice(mk, org_id)

    async with mk() as s:
        row = await s.get(Invoice, inv.id)
        exc = await create_exception(
            s,
            exception_type="duplicate",
            severity="error",
            description="Looks like a duplicate of INV-EXCAUDIT-001 from Globex Corporation",
            organization_id=org_id,
            invoice=row,
        )
        exc_id = exc.id
        await s.commit()

    # Correlation-keyed to the INVOICE — this is what puts the row on
    # GET /api/audit/invoice/{id} next to invoice.approved / invoice.rejected.
    rows = await _audit_rows(mk, correlation_id=inv.correlation_id, action="exception.raised")
    assert len(rows) == 1
    entry = rows[0]
    assert entry.entity_type == "exception"
    assert entry.entity_id == exc_id
    assert entry.actor_id is None, "a detector opened this, not a person"

    details = entry.details
    assert details["exception_id"] == str(exc_id)
    assert details["exception_type"] == "duplicate"
    assert details["severity"] == "error"
    assert details["invoice_id"] == str(inv.id)
    assert details["new_status"] == "open"
    # `duplicate` is one of the three types that blocks a payment run.
    assert details["payment_blocking"] is True
    # The generated description can name a vendor; the exception row holds it,
    # the trail does not duplicate it.
    assert "description" not in details
    assert "Globex" not in str(details)


@pytest.mark.asyncio
async def test_non_blocking_exception_type_is_flagged_as_such(realdb):
    from app.services.exception_service import create_exception

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv = await _make_invoice(mk, org_id, number="INV-EXCAUDIT-002")

    async with mk() as s:
        row = await s.get(Invoice, inv.id)
        await create_exception(
            s,
            exception_type="po_mismatch",
            severity="warning",
            description="PO total differs",
            organization_id=org_id,
            invoice=row,
        )
        await s.commit()

    rows = await _audit_rows(mk, correlation_id=inv.correlation_id, action="exception.raised")
    assert len(rows) == 1
    assert rows[0].details["payment_blocking"] is False


@pytest.mark.asyncio
async def test_payment_blocking_flag_tracks_the_real_gate():
    """The audit flag must be derived from the payment-run gate, not a copy.

    If someone adds a type to PAYMENT_BLOCKING_EXCEPTION_TYPES, the trail's
    `payment_blocking` has to follow automatically — a second hardcoded list
    would silently under-report which decisions unblocked money.
    """
    from app.api.payments import PAYMENT_BLOCKING_EXCEPTION_TYPES
    from app.services.exception_lifecycle import is_payment_blocking

    for exception_type in PAYMENT_BLOCKING_EXCEPTION_TYPES:
        assert is_payment_blocking(exception_type) is True
    assert is_payment_blocking("po_mismatch") is False
    assert is_payment_blocking("totally_unknown_type") is False


@pytest.mark.asyncio
async def test_invoice_less_exception_audits_under_its_own_correlation(realdb):
    """A Positive Pay `not_on_file` cheque has no invoice to correlate to — the
    exception's own id groups its lifecycle rows instead of losing them."""
    from app.services.exception_service import create_exception

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    async with mk() as s:
        exc = await create_exception(
            s,
            exception_type="fraud_flag",
            severity="error",
            description="Cheque 9911 cleared but was never issued",
            organization_id=org_id,
        )
        exc_id = exc.id
        await s.commit()

    rows = await _audit_rows(mk, correlation_id=exc_id, action="exception.raised")
    assert len(rows) == 1
    assert rows[0].entity_id == exc_id
    assert rows[0].details["invoice_id"] is None
    assert rows[0].details["payment_blocking"] is True
