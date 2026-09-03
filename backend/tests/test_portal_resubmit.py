"""Supplier portal — revise & resubmit a rejected invoice.

persona-supplier finding (issue #328): a rejected invoice was a dead end — the
only way forward was uploading a brand-new file, which risks a duplicate flag
against the original and loses the thread. `POST /portal/invoices/{id}/resubmit`
swaps the file on the SAME row, resolves the open `review_rejected` exception,
and puts it straight back to `ready_for_review` (no re-extraction — that could
re-link the invoice to a different vendor and drop it out of the portal).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.deps import create_vendor_access_token
from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser
from app.models.workflow import AuditLog

TENANT = "a"
PDF = ("fix.pdf", b"%PDF-1.4\n corrected invoice \n%%EOF", "application/pdf")


async def _seed(mk, org_id):
    vendor_id, vu_id = uuid.uuid4(), uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=vendor_id,
                name="Resubmit Co",
                organization_id=org_id,
                status="active",
                source="manual",
            )
        )
        s.add(
            VendorUser(
                id=vu_id,
                vendor_id=vendor_id,
                email=f"{vu_id}@portal.test",
                full_name="Portal User",
                hashed_password="x",
                is_active=True,
            )
        )
        await s.commit()
    return vendor_id, vu_id


async def _add_invoice(mk, org_id, vendor_id, *, status, number="RS-1", file_key=None) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=number,
                vendor_name="Resubmit Co",
                vendor_id=vendor_id,
                amount=Decimal("10.00"),
                currency="USD",
                status=status,
                file_key=file_key or f"{org_id}/{inv_id}/original.pdf",
                organization_id=org_id,
            )
        )
        await s.commit()
    return inv_id


async def _add_rejection_exc(mk, org_id, inv_id) -> uuid.UUID:
    exc_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            APException(
                id=exc_id,
                invoice_id=inv_id,
                exception_type="review_rejected",
                severity="warning",
                description="Wrong PO number",
                status="open",
                organization_id=org_id,
            )
        )
        await s.commit()
    return exc_id


def _client(realdb, vu_id, vendor_id):
    token = create_vendor_access_token(vu_id, vendor_id)
    c = realdb.client(key=TENANT, role=None)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.mark.asyncio
async def test_resubmit_swaps_file_and_reenters_review(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed(mk, org_id)
    inv_id = await _add_invoice(mk, org_id, vendor_id, status=InvoiceStatus.rejected)
    exc_id = await _add_rejection_exc(mk, org_id, inv_id)

    async with _client(realdb, vu_id, vendor_id) as client:
        resp = await client.post(f"/api/portal/invoices/{inv_id}/resubmit", files={"file": PDF})
    assert resp.status_code == 202, resp.text
    assert resp.json()["id"] == str(inv_id)
    assert resp.json()["status"] == "ready_for_review"

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.ready_for_review
        # Same row — the invoice stays this vendor's, keeps its number.
        assert inv.vendor_id == vendor_id
        assert inv.invoice_number == "RS-1"
        assert inv.file_key and inv.file_key.endswith("fix.pdf")

        exc = (await s.execute(select(APException).where(APException.id == exc_id))).scalar_one()
        assert exc.status == "resolved"

        actions = {r.action for r in (await s.execute(select(AuditLog))).scalars().all()}
    assert "invoice.resubmitted_by_vendor" in actions


@pytest.mark.asyncio
async def test_resubmit_409s_unless_rejected(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed(mk, org_id)
    inv_id = await _add_invoice(mk, org_id, vendor_id, status=InvoiceStatus.approved)

    async with _client(realdb, vu_id, vendor_id) as client:
        resp = await client.post(f"/api/portal/invoices/{inv_id}/resubmit", files={"file": PDF})
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_resubmit_cannot_touch_another_vendors_invoice(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    mine_vendor, mine_vu = await _seed(mk, org_id)

    other_vendor = uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=other_vendor,
                name="Other",
                organization_id=org_id,
                status="active",
                source="manual",
            )
        )
        await s.commit()
    their_inv = await _add_invoice(
        mk, org_id, other_vendor, status=InvoiceStatus.rejected, number="RS-OTHER"
    )

    async with _client(realdb, mine_vu, mine_vendor) as client:
        resp = await client.post(f"/api/portal/invoices/{their_inv}/resubmit", files={"file": PDF})
    assert resp.status_code == 404, resp.text
