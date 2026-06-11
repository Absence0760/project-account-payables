"""Real-DB coverage for GET /api/invoices/counts.

The list-page status chips ("All" + per-status) read this endpoint. It must
tally the *whole* tenant via a server-side GROUP BY — the previous
implementation counted client-side over the first page of results, so a
tenant with more invoices than the page window undercounted every chip.

These tests pin: an empty tenant returns zeros, mixed statuses tally per
status, and a status with more rows than the list page_size (25) is counted
in full — the exact case the old page-1 tally got wrong.
"""

from decimal import Decimal

import pytest

from app.models.invoice import Invoice, InvoiceStatus


async def _add_invoices(mk, org_id, status: InvoiceStatus, n: int) -> None:
    async with mk() as s:
        for i in range(n):
            s.add(
                Invoice(
                    organization_id=org_id,
                    invoice_number=f"INV-{status.value}-{i}",
                    vendor_name="Acme",
                    amount=Decimal("100.00"),
                    status=status,
                )
            )
        await s.commit()


@pytest.mark.asyncio
async def test_counts_empty_tenant(realdb):
    async with realdb.client(key="a") as c:
        resp = await c.get("/api/invoices/counts")
    assert resp.status_code == 200
    assert resp.json() == {"counts": {}, "total": 0}


@pytest.mark.asyncio
async def test_counts_group_by_status_full_tenant(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    # 30 'new' exceeds the list page_size (25): the old client-side tally
    # over page 1 would have reported at most 25 here.
    await _add_invoices(mk, org_id, InvoiceStatus.new, 30)
    await _add_invoices(mk, org_id, InvoiceStatus.approved, 4)
    await _add_invoices(mk, org_id, InvoiceStatus.paid, 2)

    async with realdb.client(key="a") as c:
        resp = await c.get("/api/invoices/counts")

    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {"new": 30, "approved": 4, "paid": 2}
    assert body["total"] == 36


@pytest.mark.asyncio
async def test_counts_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/invoices/counts")
    assert resp.status_code == 401
