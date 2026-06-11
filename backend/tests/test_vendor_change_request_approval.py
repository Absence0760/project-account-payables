"""AP-admin approval of supplier-portal change requests.

The approval gate is the fraud control: a staged bank/tax change applies to
the vendor ONLY when an AP admin approves it, and rejection never touches the
vendor. RBAC: a clerk/cfo cannot approve. All DB-backed via `realdb`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.vendor import Vendor
from app.models.vendor_change_request import VendorChangeRequest

TENANT = "a"


@pytest.fixture
async def mk(realdb):
    """One tenant sessionmaker per test on a single engine, disposed at end."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.database import _make_tenant_url

    engine = create_async_engine(_make_tenant_url(realdb.info(TENANT).db_name), poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed_vendor(mk, org_id, **kw) -> uuid.UUID:
    vendor_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=vendor_id,
                name=kw.get("name", "Acme Supply"),
                organization_id=org_id,
                status="active",
                source="manual",
                bank_details=kw.get("bank_details"),
                tax_id=kw.get("tax_id"),
                tin_verified_at=kw.get("tin_verified_at"),
            )
        )
        await s.commit()
    return vendor_id


async def _stage(mk, org_id, vendor_id, change_type, proposed_value) -> uuid.UUID:
    req_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            VendorChangeRequest(
                id=req_id,
                vendor_id=vendor_id,
                organization_id=org_id,
                requested_by_vendor_user_id=uuid.uuid4(),
                change_type=change_type,
                status="pending",
                proposed_value=proposed_value,
            )
        )
        await s.commit()
    return req_id


@pytest.mark.asyncio
async def test_approve_bank_change_applies_to_vendor(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, org_id, bank_details={"bank_name": "Old Bank"})
    req_id = await _stage(
        mk,
        org_id,
        vendor_id,
        "bank_details",
        {"bank_details": {"account_number": "12345678", "bank_name": "New Bank"}},
    )
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(f"/api/vendors/change-requests/{req_id}/approve")
    assert resp.status_code == 200, resp.text

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        # _merge_bank_details merged the incoming keys onto the existing dict.
        assert v.bank_details["account_number"] == "12345678"
        assert v.bank_details["bank_name"] == "New Bank"
        req = (
            await s.execute(select(VendorChangeRequest).where(VendorChangeRequest.id == req_id))
        ).scalar_one()
        assert req.status == "approved"
        assert req.reviewed_by_user_id is not None


@pytest.mark.asyncio
async def test_approve_tax_id_change_clears_tin_verification(realdb):
    from datetime import UTC, datetime

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, org_id, tax_id="old", tin_verified_at=datetime.now(UTC))
    req_id = await _stage(mk, org_id, vendor_id, "tax_id", {"tax_id": "99-9999999"})
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(f"/api/vendors/change-requests/{req_id}/approve")
    assert resp.status_code == 200

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert v.tax_id == "99-9999999"
        assert v.tin_verified_at is None  # re-verification required


@pytest.mark.asyncio
async def test_reject_leaves_vendor_untouched(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, org_id, bank_details={"bank_name": "Old Bank"})
    req_id = await _stage(
        mk, org_id, vendor_id, "bank_details", {"bank_details": {"account_number": "55554444"}}
    )
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/vendors/change-requests/{req_id}/reject",
            json={"review_note": "Could not verify by phone"},
        )
    assert resp.status_code == 200

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert v.bank_details == {"bank_name": "Old Bank"}  # unchanged
        req = (
            await s.execute(select(VendorChangeRequest).where(VendorChangeRequest.id == req_id))
        ).scalar_one()
        assert req.status == "rejected"
        assert req.review_note == "Could not verify by phone"


@pytest.mark.asyncio
async def test_double_approve_409(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, org_id)
    req_id = await _stage(
        mk, org_id, vendor_id, "bank_details", {"bank_details": {"account_number": "1"}}
    )
    async with realdb.client(key=TENANT, role="admin") as client:
        first = await client.post(f"/api/vendors/change-requests/{req_id}/approve")
        second = await client.post(f"/api/vendors/change-requests/{req_id}/approve")
    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_clerk_cannot_approve(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, org_id)
    req_id = await _stage(
        mk, org_id, vendor_id, "bank_details", {"bank_details": {"account_number": "1"}}
    )
    async with realdb.client(key=TENANT, role="ap_clerk") as client:
        resp = await client.post(f"/api/vendors/change-requests/{req_id}/approve")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_change_requests_masks_value(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, org_id)
    await _stage(
        mk,
        org_id,
        vendor_id,
        "bank_details",
        {"bank_details": {"account_number": "12349876", "bank_name": "Bank"}},
    )
    async with realdb.client(key=TENANT, role="ap_manager") as client:
        resp = await client.get("/api/vendors/change-requests")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The pending queue masks the value — full account number must be absent.
    assert "12349876" not in resp.text
    mine = [it for it in body["items"] if it["vendor_id"] == str(vendor_id)]
    assert mine, "the staged request should appear in the pending queue"
    assert mine[0]["proposed_value"].get("account_last4") == "9876"


@pytest.mark.asyncio
async def test_change_requests_literal_route_not_shadowed(realdb):
    """`GET /vendors/change-requests` must hit the queue handler, not the
    `/{vendor_id}` route (which would 422 on the non-UUID segment)."""
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get("/api/vendors/change-requests")
    assert resp.status_code == 200
    assert "items" in resp.json()
