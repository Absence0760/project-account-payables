"""Supplier portal — a vendor can see WHY their invoice was rejected.

persona-supplier finding (issue #328): the rejection reason existed (audit log,
`review_rejected` exception, rejection email) but never reached the portal API,
so a supplier had no in-app way to learn what to fix. `GET /portal/invoices`
and `GET /portal/invoices/{id}` now carry `rejection_reason` for a rejected
invoice — the exception's description, never the rejecting employee's name.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.api.deps import create_vendor_access_token
from app.models.exception import Exception as APException
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
                name="Reason Co",
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


async def _add_invoice(mk, org_id, vendor_id, *, number, status) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=number,
                vendor_name="Reason Co",
                vendor_id=vendor_id,
                amount=Decimal("10.00"),
                currency="USD",
                status=status,
                rejected_by="Alice Approver",
                organization_id=org_id,
            )
        )
        await s.commit()
    return inv_id


async def _add_rejection_exc(mk, org_id, inv_id, *, description, created_at) -> None:
    async with mk() as s:
        s.add(
            APException(
                id=uuid.uuid4(),
                invoice_id=inv_id,
                exception_type="review_rejected",
                severity="warning",
                description=description,
                status="open",
                resolved_by="Alice Approver",
                organization_id=org_id,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        await s.commit()


def _client(realdb, vu_id, vendor_id):
    token = create_vendor_access_token(vu_id, vendor_id)
    c = realdb.client(key=TENANT, role=None)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.mark.asyncio
async def test_rejection_reason_surfaces_for_a_rejected_invoice(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed(mk, org_id)

    rej = await _add_invoice(mk, org_id, vendor_id, number="RR-1", status=InvoiceStatus.rejected)
    await _add_invoice(mk, org_id, vendor_id, number="RR-2", status=InvoiceStatus.new)
    now = datetime.now(UTC)
    # Two rejections over time — the newest reason is the one that shows.
    await _add_rejection_exc(
        mk, org_id, rej, description="PO number missing", created_at=now - timedelta(days=3)
    )
    await _add_rejection_exc(
        mk, org_id, rej, description="Amount does not match the PO", created_at=now
    )

    async with _client(realdb, vu_id, vendor_id) as client:
        body = (await client.get("/api/portal/invoices")).json()
        by_num = {i["invoice_number"]: i for i in body["items"]}
        assert by_num["RR-1"]["rejection_reason"] == "Amount does not match the PO"
        assert by_num["RR-2"]["rejection_reason"] is None

        detail = (await client.get(f"/api/portal/invoices/{rej}")).json()
        assert detail["rejection_reason"] == "Amount does not match the PO"

    # The rejecting employee's name never rides along.
    assert "Alice Approver" not in str(body)


@pytest.mark.asyncio
async def test_no_reason_leaks_once_the_invoice_moves_off_rejected(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed(mk, org_id)

    # Reworked invoice: back to review, but the old exception row still exists.
    inv = await _add_invoice(
        mk, org_id, vendor_id, number="RR-3", status=InvoiceStatus.ready_for_review
    )
    await _add_rejection_exc(
        mk, org_id, inv, description="was rejected earlier", created_at=datetime.now(UTC)
    )

    async with _client(realdb, vu_id, vendor_id) as client:
        body = (await client.get("/api/portal/invoices")).json()
        assert body["items"][0]["rejection_reason"] is None
        detail = (await client.get(f"/api/portal/invoices/{inv}")).json()
        assert detail["rejection_reason"] is None
