"""Supplier portal — the `waiting_on` "why is my invoice stuck" signal.

persona-supplier finding (issue #328): a portal invoice in a processing phase
showed only the phase chip. `GET /portal/invoices[/{id}]` now carries a
PII-free `waiting_on` bucket (`review` / `processing` / `erp`) + `waiting_on_days`
while the invoice is in one of those phases, and nothing otherwise.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.api.deps import create_vendor_access_token
from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser

TENANT = "a"


async def _seed(mk, org_id):
    vendor_id, vu_id = uuid.uuid4(), uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=vendor_id,
                name="Stuck Co",
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


async def _add(mk, org_id, vendor_id, *, number, status, updated_days_ago=0):
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=number,
                vendor_name="Stuck Co",
                vendor_id=vendor_id,
                amount=Decimal("10.00"),
                currency="USD",
                status=status,
                organization_id=org_id,
            )
        )
        await s.flush()
        if updated_days_ago:
            ts = datetime.now(UTC) - timedelta(days=updated_days_ago)
            await s.execute(update(Invoice).where(Invoice.id == inv_id).values(updated_at=ts))
        await s.commit()
    return inv_id


def _client(realdb, vu_id, vendor_id):
    token = create_vendor_access_token(vu_id, vendor_id)
    c = realdb.client(key=TENANT, role=None)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.mark.asyncio
async def test_waiting_on_set_for_processing_phases_only(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed(mk, org_id)

    await _add(
        mk,
        org_id,
        vendor_id,
        number="W-REVIEW",
        status=InvoiceStatus.ready_for_review,
        updated_days_ago=5,
    )
    await _add(mk, org_id, vendor_id, number="W-ERP", status=InvoiceStatus.sent_to_erp)
    await _add(mk, org_id, vendor_id, number="W-NEW", status=InvoiceStatus.new)
    await _add(mk, org_id, vendor_id, number="W-PAID", status=InvoiceStatus.paid)
    await _add(mk, org_id, vendor_id, number="W-REJ", status=InvoiceStatus.rejected)

    async with _client(realdb, vu_id, vendor_id) as client:
        body = (await client.get("/api/portal/invoices")).json()
    by = {i["invoice_number"]: i for i in body["items"]}

    assert by["W-REVIEW"]["waiting_on"] == "review"
    assert by["W-REVIEW"]["waiting_on_days"] >= 5
    assert by["W-ERP"]["waiting_on"] == "erp"
    for n in ("W-NEW", "W-PAID", "W-REJ"):
        assert by[n]["waiting_on"] is None
        assert by[n]["waiting_on_days"] is None

    # No internal status string or user name leaks via the new field.
    assert "ready_for_review" not in str([i["waiting_on"] for i in body["items"]])


@pytest.mark.asyncio
async def test_waiting_on_on_the_detail_endpoint_too(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed(mk, org_id)
    inv_id = await _add(mk, org_id, vendor_id, number="W-1", status=InvoiceStatus.pending)

    async with _client(realdb, vu_id, vendor_id) as client:
        detail = (await client.get(f"/api/portal/invoices/{inv_id}")).json()
    assert detail["waiting_on"] == "processing"
    assert isinstance(detail["waiting_on_days"], int)
