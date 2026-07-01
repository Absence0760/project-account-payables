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
def mk(realdb):
    """Tenant sessionmaker on the realdb-managed engine — the shared pattern
    (see test_portal_self_service.py / test_credit_memos.py). Avoids a second
    engine against the same DB, which thrashes the pool and leaks past the
    harness's per-test cleanup."""
    return realdb.sessionmaker(TENANT)


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


# ===========================================================================
# AP-initiated bank-detail changes are dual-control (BEC / bank-redirect gate).
# An AP user can PROPOSE a bank change but it stages a pending request instead
# of applying; a SECOND user (not the proposer) must approve it.
# ===========================================================================


@pytest.mark.asyncio
async def test_ap_patch_bank_details_stages_instead_of_applying(realdb):
    """PATCH /vendors/{id} with bank_details must NOT apply it inline — it
    stages a pending change request stamped with the AP requester. The vendor's
    bank_details stay put until a second approver signs off."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={"counterparty_id": "cp_old"})

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.patch(
            f"/api/vendors/{vendor_id}",
            json={"bank_details": {"counterparty_id": "cp_attacker"}},
        )
    assert resp.status_code == 200, resp.text
    # Response shows the UNCHANGED bank details (the change is only staged).
    assert (resp.json().get("bank_details") or {}).get("counterparty_id") == "cp_old"

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert (v.bank_details or {}).get("counterparty_id") == "cp_old", "bank must NOT change"
        req = (
            await s.execute(
                select(VendorChangeRequest).where(VendorChangeRequest.vendor_id == vendor_id)
            )
        ).scalar_one()
        assert req.status == "pending"
        assert req.change_type == "bank_details"
        assert req.requested_by_user_id == info.users["admin"]
        assert req.requested_by_vendor_user_id is None  # AP-initiated, not portal


@pytest.mark.asyncio
async def test_ap_bank_change_endpoint_stages_and_returns_202(realdb):
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={"counterparty_id": "cp_old"})

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/vendors/{vendor_id}/bank-change",
            json={"bank_details": {"counterparty_id": "cp_new"}},
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "pending"

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert (v.bank_details or {}).get("counterparty_id") == "cp_old", "still unapplied"


@pytest.mark.asyncio
async def test_requester_cannot_approve_their_own_bank_change(realdb):
    """Segregation of duties: the admin who proposed the change can't approve
    it — that would collapse dual control back to a one-person bank redirect."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={"counterparty_id": "cp_old"})

    async with realdb.client(key=TENANT, role="admin") as client:
        staged = await client.post(
            f"/api/vendors/{vendor_id}/bank-change",
            json={"bank_details": {"counterparty_id": "cp_new"}},
        )
        req_id = staged.json()["id"]
        # Same admin tries to approve their own request.
        resp = await client.post(f"/api/vendors/change-requests/{req_id}/approve")
    assert resp.status_code == 403, resp.text

    async with mk() as s:
        req = (
            await s.execute(select(VendorChangeRequest).where(VendorChangeRequest.id == req_id))
        ).scalar_one()
        assert req.status == "pending", "a self-approval must not resolve the request"
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert (v.bank_details or {}).get("counterparty_id") == "cp_old", "still unapplied"


@pytest.mark.asyncio
async def test_a_different_approver_applies_the_ap_bank_change(realdb):
    """The happy path: admin proposes, a DIFFERENT approver (ap_manager) signs
    off, and only then does the change apply."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={"counterparty_id": "cp_old"})

    async with realdb.client(key=TENANT, role="admin") as client:
        staged = await client.post(
            f"/api/vendors/{vendor_id}/bank-change",
            json={"bank_details": {"counterparty_id": "cp_new"}},
        )
        req_id = staged.json()["id"]

    async with realdb.client(key=TENANT, role="ap_manager") as approver:
        resp = await approver.post(f"/api/vendors/change-requests/{req_id}/approve")
    assert resp.status_code == 200, resp.text

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert (v.bank_details or {}).get("counterparty_id") == "cp_new", "now applied"


@pytest.mark.asyncio
async def test_bank_change_approval_flags_payable_invoices(realdb):
    """BEC gate: approving a bank-detail change must raise a fraud_flag on every
    invoice already in the payment queue for that vendor, so the next run gets a
    human second look before paying into the new account."""
    from decimal import Decimal

    from app.models.exception import Exception as APException
    from app.models.invoice import Invoice, InvoiceStatus

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, org_id, bank_details={"bank_name": "Old Bank"})

    # One invoice in the payment queue (approved) and one still in review.
    payable_id, review_id = uuid.uuid4(), uuid.uuid4()
    async with mk() as s:
        s.add_all(
            [
                Invoice(
                    id=payable_id,
                    organization_id=org_id,
                    vendor_id=vendor_id,
                    invoice_number="BEC-PAYABLE",
                    vendor_name="Acme Supply",
                    amount=Decimal("75000.00"),
                    currency="USD",
                    status=InvoiceStatus.approved,
                ),
                Invoice(
                    id=review_id,
                    organization_id=org_id,
                    vendor_id=vendor_id,
                    invoice_number="BEC-REVIEW",
                    vendor_name="Acme Supply",
                    amount=Decimal("10.00"),
                    currency="USD",
                    status=InvoiceStatus.ready_for_review,
                ),
            ]
        )
        await s.commit()

    req_id = await _stage(
        mk,
        org_id,
        vendor_id,
        "bank_details",
        {"bank_details": {"account_number": "99999999", "bank_name": "Attacker Bank"}},
    )
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(f"/api/vendors/change-requests/{req_id}/approve")
    assert resp.status_code == 200, resp.text

    async with mk() as s:
        # The payable invoice got a fraud_flag payment hold.
        payable_flags = (
            (
                await s.execute(
                    select(APException).where(
                        APException.invoice_id == payable_id,
                        APException.exception_type == "fraud_flag",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(payable_flags) == 1
        assert "bank details" in (payable_flags[0].description or "").lower()
        # The in-review invoice (not in the payment queue) is NOT flagged.
        review_flags = (
            (
                await s.execute(
                    select(APException).where(
                        APException.invoice_id == review_id,
                        APException.exception_type == "fraud_flag",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert review_flags == []
