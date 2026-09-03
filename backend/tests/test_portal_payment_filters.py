"""Supplier-portal payment-history list — vendor-facing status + number filters.

Mirror of `test_portal_invoice_filters.py` for `GET /api/portal/payments`: the
filter is a data-layer clause on a vendor-scoped, invoice-joined query, and the
guarantee is that it can only narrow — never reach another vendor's payment.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.api.deps import create_vendor_access_token
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser

TENANT = "a"


async def _seed_vendor_and_user(mk, org_id) -> tuple[uuid.UUID, uuid.UUID]:
    vendor_id, vu_id = uuid.uuid4(), uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=vendor_id,
                name="Pay Filter Co",
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


async def _add_paid_invoice(mk, org_id, vendor_id, *, number: str, pay_status: str) -> None:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=number,
                vendor_name="Pay Filter Co",
                vendor_id=vendor_id,
                amount=Decimal("10.00"),
                currency="USD",
                status=InvoiceStatus.paid,
                organization_id=org_id,
            )
        )
        await s.flush()
        s.add(
            Payment(
                id=uuid.uuid4(),
                invoice_id=inv_id,
                amount=Decimal("10.00"),
                method="ach",
                status=pay_status,
            )
        )
        await s.commit()


def _portal_client(realdb, vu_id, vendor_id):
    token = create_vendor_access_token(vu_id, vendor_id)
    client = realdb.client(key=TENANT, role=None)
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.mark.asyncio
async def test_payment_status_filter_and_number_search(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed_vendor_and_user(mk, org_id)

    await _add_paid_invoice(mk, org_id, vendor_id, number="PF-DONE", pay_status="completed")
    await _add_paid_invoice(mk, org_id, vendor_id, number="PF-FAIL", pay_status="failed")
    await _add_paid_invoice(mk, org_id, vendor_id, number="PF-PEND", pay_status="pending")

    async with _portal_client(realdb, vu_id, vendor_id) as client:
        resp = await client.get("/api/portal/payments", params={"status": "completed"})
        assert resp.status_code == 200, resp.text
        assert [r["invoice_number"] for r in resp.json()["items"]] == ["PF-DONE"]

        resp = await client.get(
            "/api/portal/payments", params=[("status", "failed"), ("status", "pending")]
        )
        assert sorted(r["invoice_number"] for r in resp.json()["items"]) == ["PF-FAIL", "PF-PEND"]

        resp = await client.get("/api/portal/payments", params={"search": "pf-done"})
        assert [r["invoice_number"] for r in resp.json()["items"]] == ["PF-DONE"]

        # Unknown status → ignored, not 422.
        resp = await client.get("/api/portal/payments", params={"status": "bogus"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 3


@pytest.mark.asyncio
async def test_payment_filters_stay_within_the_callers_vendor(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    mine_vendor, mine_vu = await _seed_vendor_and_user(mk, org_id)

    other_vendor = uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=other_vendor,
                name="Not Mine",
                organization_id=org_id,
                status="active",
                source="manual",
            )
        )
        await s.commit()
    await _add_paid_invoice(mk, org_id, other_vendor, number="THEIRS", pay_status="completed")
    await _add_paid_invoice(mk, org_id, mine_vendor, number="MINE", pay_status="pending")

    async with _portal_client(realdb, mine_vu, mine_vendor) as client:
        resp = await client.get(
            "/api/portal/payments", params={"status": "completed", "search": "THEIRS"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0
