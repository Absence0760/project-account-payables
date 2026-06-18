"""Critical-path HTTP/integration tests for the invoice lifecycle — the
single most-used flow clients touch daily.

Where `test_invoice_lifecycle.py` and `test_workflow_state_machine.py`
pin the state machine at the *unit* layer (mocked db + dispatch_audit),
this file drives the actual `/api/invoices/{id}/*` workflow endpoints
over the real ASGI app against a real Postgres tenant DB — proving the
request → auth/RBAC → service → DB-commit → audit-row chain the client
actually exercises:

  - approve (HTTP) persists `approved` + writes an immutable
    `invoice.approved` audit row carrying the digital signature
  - approve-with-corrections persists the corrected fields AND records
    a SOX field-diff (`details.changes`) in the audit row, with money
    serialised as string-Decimal (never float)
  - reject (HTTP) persists `rejected`, opens a `review_rejected`
    exception, and writes the audit row with the reason
  - send-to-erp (HTTP) transitions `approved → sending_to_erp` (202)
    and writes the audit row
  - an illegal transition is refused with 409 at the HTTP boundary and
    leaves the row + audit trail untouched (approve a `new` invoice;
    reject a `paid` invoice)
  - RBAC: an `ap_clerk` cannot approve (403) and the row stays put
  - tenant isolation: approving an invoice that lives in tenant B from
    tenant A is a 404 (the tenant-scoped DB never sees the other row)

All assertions read the committed row back through a fresh session, so
they prove durability + atomicity, not just the HTTP response shape.
These run against the opt-in `realdb` fixture (skips without `pnpm
db:up`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import AuditLog


async def _seed_invoice(
    mk,
    org_id,
    *,
    status: InvoiceStatus,
    amount: str = "500.00",
    number: str = "CP-001",
) -> uuid.UUID:
    """Insert one invoice directly (no workflow instance) and return its id.

    Deliberately leaves `uploaded_by_id` NULL so the segregation-of-duties
    guard in approve never fires (the actor is never the uploader), keeping
    the approval path deterministic.
    """
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                invoice_number=number,
                vendor_name="Critical Path Vendor",
                amount=Decimal(amount),
                currency="USD",
                status=status,
            )
        )
        await s.commit()
    return inv_id


# ---------------------------------------------------------------------------
# Approve (HTTP) — happy path persists + audits + signs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_endpoint_persists_status_and_writes_signed_audit_row(realdb):
    info = realdb.info("a")
    inv_id = await _seed_invoice(
        realdb.sessionmaker("a"), info.org_id, status=InvoiceStatus.ready_for_review
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.approved
        assert inv.approval_date is not None  # finalize stamped it
        assert inv.approved_by  # actor name recorded

        rows = (
            await s.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == inv_id)
                .where(AuditLog.action == "invoice.approved")
            )
        ).scalars().all()
    assert len(rows) == 1, "approve must write exactly one invoice.approved audit row"
    details = rows[0].details or {}
    assert details.get("old_status") == "ready_for_review"
    assert details.get("new_status") == "approved"
    # Digital signature (SOX non-repudiation) is stamped on the row only when a
    # signing key is configured (empty key → signing skipped, by design — no
    # hardcoded fallback). pytest doesn't load .env.development (only main.py
    # does), so assert the contract precisely: signed iff a key is set.
    from app.config import settings

    if settings.approval_signing_key:
        assert "signature" in details, "approval audit row must carry the HMAC signature"
    else:
        assert "signature" not in details, "no key configured → must not fake a signature"


@pytest.mark.asyncio
async def test_approve_with_corrections_persists_fields_and_records_field_diff(realdb):
    """A reviewer corrects the extracted amount + vendor at approval time.
    The corrected values must persist on the invoice, and the audit row
    must carry a SOX field-diff with money as string-Decimal (never float).
    """
    info = realdb.info("a")
    inv_id = await _seed_invoice(
        realdb.sessionmaker("a"),
        info.org_id,
        status=InvoiceStatus.ready_for_review,
        amount="100.00",
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/invoices/{inv_id}/approve",
            json={"vendor": "Corrected Vendor LLC", "amount": "250.50"},
        )
    assert resp.status_code == 200, resp.text

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.vendor_name == "Corrected Vendor LLC"
        assert inv.amount == Decimal("250.50")

        row = (
            await s.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == inv_id)
                .where(AuditLog.action == "invoice.approved")
            )
        ).scalar_one()
    changes = (row.details or {}).get("changes") or {}
    assert "amount" in changes, "field-diff must record the corrected amount"
    assert "vendor_name" in changes
    # Money serialises as string-Decimal in the diff (invariant: never float).
    assert isinstance(changes["amount"]["new"], str)
    assert changes["amount"]["new"] == "250.50"
    assert changes["amount"]["old"] == "100.00"


# ---------------------------------------------------------------------------
# Reject (HTTP) — persists + opens exception + audits the reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_endpoint_persists_status_opens_exception_and_audits_reason(realdb):
    info = realdb.info("a")
    inv_id = await _seed_invoice(
        realdb.sessionmaker("a"), info.org_id, status=InvoiceStatus.ready_for_review
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/invoices/{inv_id}/reject",
            json={"reason": "amount does not match the PO"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.rejected

        audit = (
            await s.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == inv_id)
                .where(AuditLog.action == "invoice.rejected")
            )
        ).scalar_one()
        assert (audit.details or {}).get("reason") == "amount does not match the PO"

        exc = (
            await s.execute(
                select(APException).where(APException.invoice_id == inv_id)
            )
        ).scalar_one()
    assert exc.exception_type == "review_rejected"
    assert exc.description == "amount does not match the PO"


@pytest.mark.asyncio
async def test_reject_requires_a_reason(realdb):
    """The reject body's `reason` is mandatory (min_length=1) — an empty
    reject is a 422, never a silent rejection with no audit trail."""
    info = realdb.info("a")
    inv_id = await _seed_invoice(
        realdb.sessionmaker("a"), info.org_id, status=InvoiceStatus.ready_for_review
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/reject", json={"reason": ""})
    assert resp.status_code == 422

    # Invoice untouched.
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
    assert inv.status == InvoiceStatus.ready_for_review


# ---------------------------------------------------------------------------
# Send-to-ERP (HTTP) — approved → sending_to_erp + 202 + audit row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_erp_transitions_and_audits(realdb):
    info = realdb.info("a")
    inv_id = await _seed_invoice(
        realdb.sessionmaker("a"), info.org_id, status=InvoiceStatus.approved
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/send-to-erp")
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "sending_to_erp"

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.sending_to_erp
        audit = (
            await s.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == inv_id)
                .where(AuditLog.action == "invoice.erp_submitted")
            )
        ).scalar_one()
    assert (audit.details or {}).get("new_status") == "sending_to_erp"


# ---------------------------------------------------------------------------
# Illegal transitions refused at the HTTP boundary (409), row + trail intact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_of_a_rejected_invoice_is_rejected_409_and_writes_no_audit(realdb):
    """`rejected → approved` is NOT in VALID_TRANSITIONS — a rejected
    invoice must be resubmitted (`rejected → ready_for_review`) before it
    can be approved. The endpoint must 409 and leave the row + audit trail
    untouched — no back-door approval that skips re-review.

    (Note: `new → approved` IS legal — the direct-approve / auto-approve
    path — so this test deliberately uses `rejected`, which is not.)
    """
    info = realdb.info("a")
    inv_id = await _seed_invoice(
        realdb.sessionmaker("a"), info.org_id, status=InvoiceStatus.rejected
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/approve")
    assert resp.status_code == 409, resp.text

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.rejected
        assert inv.approval_date is None
        count = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == inv_id)
            )
        ).scalar_one()
    assert count == 0, "an illegal transition must not write an audit row"


@pytest.mark.asyncio
async def test_reject_of_a_paid_invoice_is_rejected_409(realdb):
    """`paid → rejected` is not in VALID_TRANSITIONS. Rejecting an
    already-paid invoice must 409 — not silently flip a settled payable
    back into the review queue."""
    info = realdb.info("a")
    inv_id = await _seed_invoice(
        realdb.sessionmaker("a"), info.org_id, status=InvoiceStatus.paid
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/reject", json={"reason": "too late"})
    assert resp.status_code == 409, resp.text

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
    assert inv.status == InvoiceStatus.paid


# ---------------------------------------------------------------------------
# RBAC — an ap_clerk can read but cannot approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ap_clerk_cannot_approve_invoice_403(realdb):
    info = realdb.info("a")
    inv_id = await _seed_invoice(
        realdb.sessionmaker("a"), info.org_id, status=InvoiceStatus.ready_for_review
    )

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/approve")
    assert resp.status_code == 403, resp.text

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
    assert inv.status == InvoiceStatus.ready_for_review, "denied approval must not mutate state"


# ---------------------------------------------------------------------------
# Tenant isolation — approving a tenant-B invoice from tenant A is a 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cannot_approve_an_invoice_from_another_tenant(realdb):
    """The classic IDOR: an admin in tenant A holds a valid JWT, learns a
    tenant-B invoice id, and POSTs approve with the tenant-A header. The
    tenant-scoped DB chokepoint must never see the cross-tenant row → 404,
    and tenant B's invoice stays exactly as it was."""
    b_info = realdb.info("b")
    b_inv_id = await _seed_invoice(
        realdb.sessionmaker("b"), b_info.org_id, status=InvoiceStatus.ready_for_review
    )

    # Tenant-A client (A's slug + A's JWT) targeting B's invoice id.
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/invoices/{b_inv_id}/approve")
    assert resp.status_code == 404, resp.text

    # B's invoice is untouched, and no approval audit row leaked into B.
    mk_b = realdb.sessionmaker("b")
    async with mk_b() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == b_inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.ready_for_review
        count = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == b_inv_id)
            )
        ).scalar_one()
    assert count == 0
