"""POST /api/invoices/bulk/status must not bypass the approval controls.

Routing a bulk transition straight to `approved` skipped segregation-of-duties,
the max-amount cap, and the CFO gate — so an AP manager could bulk-approve their
own uploads. The fix routes `approved` targets through review.approve_invoice
(the same path the single-invoice endpoint uses), skipping any invoice that
fails a control instead of aborting the batch.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import AuditLog


async def _seed(mk, org_id, *, number, uploaded_by_id=None, status=InvoiceStatus.ready_for_review):
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                invoice_number=number,
                vendor_name="Bulk Vendor",
                amount=Decimal("500.00"),
                currency="USD",
                status=status,
                uploaded_by_id=uploaded_by_id,
            )
        )
        await s.commit()
    return inv_id


@pytest.mark.asyncio
async def test_bulk_approve_of_own_upload_is_skipped_by_segregation(realdb):
    info = realdb.info("a")
    actor_id = info.users["ap_manager"]
    # Invoice the acting ap_manager uploaded — segregation must block self-approve.
    inv_id = await _seed(realdb.sessionmaker("a"), info.org_id, number="BULK-SELF",
                         uploaded_by_id=actor_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices/bulk/status",
            json={"ids": [str(inv_id)], "status": "approved"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 0
    assert str(inv_id) in body["skipped"]

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.ready_for_review, "self-approve must be blocked"
        approved_rows = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == inv_id, AuditLog.action == "invoice.approved")
            )
        ).scalar_one()
    assert approved_rows == 0


@pytest.mark.asyncio
async def test_bulk_approve_of_other_invoice_runs_real_approve_path(realdb):
    info = realdb.info("a")
    # uploaded_by_id NULL → segregation doesn't fire; the approve path runs fully.
    inv_id = await _seed(realdb.sessionmaker("a"), info.org_id, number="BULK-OK")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices/bulk/status",
            json={"ids": [str(inv_id)], "status": "approved"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.approved
        # It went through review.approve_invoice → an `invoice.approved` audit row
        # (with the approval signature), NOT the old `invoice.bulk_status_change`.
        approved_rows = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == inv_id, AuditLog.action == "invoice.approved")
            )
        ).scalar_one()
    assert approved_rows == 1
