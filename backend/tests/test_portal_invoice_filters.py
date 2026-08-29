"""Supplier-portal invoice list — vendor-facing status + invoice-number filters.

DB-backed (`realdb`): the filter is a data-layer WHERE clause on a vendor-scoped
query, and the point of the feature (persona-supplier finding, issue #328) is
that a vendor can narrow their OWN list without ever widening it — so the test
seeds real rows across statuses/numbers and drives the HTTP endpoint.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.api.deps import create_vendor_access_token
from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser

TENANT = "a"


async def _seed_vendor_and_user(mk, org_id) -> tuple[uuid.UUID, uuid.UUID]:
    vendor_id = uuid.uuid4()
    vu_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=vendor_id,
                name="Filterable Supply Co",
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


async def _add_invoice(mk, org_id, vendor_id, *, number: str, status: InvoiceStatus) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=number,
                vendor_name="Filterable Supply Co",
                vendor_id=vendor_id,
                amount=Decimal("10.00"),
                currency="USD",
                status=status,
                organization_id=org_id,
            )
        )
        await s.commit()
    return inv_id


def _portal_client(realdb, vu_id, vendor_id):
    token = create_vendor_access_token(vu_id, vendor_id)
    client = realdb.client(key=TENANT, role=None)
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.mark.asyncio
async def test_status_filter_narrows_to_the_requested_statuses(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed_vendor_and_user(mk, org_id)

    await _add_invoice(mk, org_id, vendor_id, number="INV-NEW", status=InvoiceStatus.new)
    await _add_invoice(mk, org_id, vendor_id, number="INV-PAID", status=InvoiceStatus.paid)
    await _add_invoice(mk, org_id, vendor_id, number="INV-REJ", status=InvoiceStatus.rejected)

    async with _portal_client(realdb, vu_id, vendor_id) as client:
        # The "Paid" phase chip sends status=paid.
        resp = await client.get("/api/portal/invoices", params={"status": "paid"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert [i["invoice_number"] for i in body["items"]] == ["INV-PAID"]

        # A repeated status param is an OR (the "Processing" chip covers several).
        resp = await client.get(
            "/api/portal/invoices", params=[("status", "new"), ("status", "rejected")]
        )
        assert resp.status_code == 200
        nums = sorted(i["invoice_number"] for i in resp.json()["items"])
        assert nums == ["INV-NEW", "INV-REJ"]


@pytest.mark.asyncio
async def test_unknown_status_value_is_ignored_not_422(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed_vendor_and_user(mk, org_id)
    await _add_invoice(mk, org_id, vendor_id, number="INV-1", status=InvoiceStatus.new)

    async with _portal_client(realdb, vu_id, vendor_id) as client:
        resp = await client.get("/api/portal/invoices", params={"status": "not_a_real_status"})
    # A stale portal build must not be able to 422 the list; an unrecognised
    # value drops out and the list falls back to unfiltered.
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_search_matches_invoice_number_substring_case_insensitively(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed_vendor_and_user(mk, org_id)

    await _add_invoice(mk, org_id, vendor_id, number="ACME-2026-001", status=InvoiceStatus.new)
    await _add_invoice(mk, org_id, vendor_id, number="ACME-2026-002", status=InvoiceStatus.new)
    await _add_invoice(mk, org_id, vendor_id, number="OTHER-999", status=InvoiceStatus.new)

    async with _portal_client(realdb, vu_id, vendor_id) as client:
        resp = await client.get("/api/portal/invoices", params={"search": "acme-2026"})
        assert resp.status_code == 200, resp.text
        nums = sorted(i["invoice_number"] for i in resp.json()["items"])
        assert nums == ["ACME-2026-001", "ACME-2026-002"]

        # A LIKE metacharacter in the term is matched literally, not as a wildcard.
        resp = await client.get("/api/portal/invoices", params={"search": "%"})
        assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_filters_never_widen_past_the_callers_vendor(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    mine_vendor, mine_vu = await _seed_vendor_and_user(mk, org_id)

    other_vendor = uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=other_vendor,
                name="Someone Else",
                organization_id=org_id,
                status="active",
                source="manual",
            )
        )
        await s.commit()
    await _add_invoice(mk, org_id, other_vendor, number="THEIRS-PAID", status=InvoiceStatus.paid)
    await _add_invoice(mk, org_id, mine_vendor, number="MINE-NEW", status=InvoiceStatus.new)

    async with _portal_client(realdb, mine_vu, mine_vendor) as client:
        # Filter for exactly the other vendor's row — the vendor scope still wins.
        resp = await client.get(
            "/api/portal/invoices", params={"status": "paid", "search": "THEIRS"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0
