"""Rejected invoices are excluded from spend aggregates consistently.

The headline CFO `total_spend` already excluded rejected invoices, but the
dashboard's vendor-spend and the CFO supplier-concentration denominator did
not — so the same response disagreed with itself (a rejected invoice inflated
vendor spend / understated concentration shares). These realdb tests pin the
consistent exclusion across both surfaces.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_vendor_invoices(realdb):
    """One approved + one (larger) rejected invoice for the same vendor."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = date.today()
    async with mk() as s:
        ent = await _default_entity_id(s)
        s.add(
            Invoice(
                organization_id=org_id,
                entity_id=ent,
                invoice_number="REJX-OK",
                vendor_name="ZZ Rejection Co",
                amount=Decimal("1000.00"),
                currency="USD",
                status=InvoiceStatus.approved,
                invoice_date=today,
            )
        )
        s.add(
            Invoice(
                organization_id=org_id,
                entity_id=ent,
                invoice_number="REJX-NO",
                vendor_name="ZZ Rejection Co",
                amount=Decimal("9000.00"),
                currency="USD",
                status=InvoiceStatus.rejected,
                invoice_date=today,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_dashboard_vendor_spend_excludes_rejected(realdb):
    await _seed_vendor_invoices(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()
    by_vendor = {v["vendor"]: v["amount"] for v in body["vendor_spend"]}
    # Only the approved $1000 counts — the rejected $9000 is excluded
    # (otherwise this would be 10000.0).
    assert by_vendor.get("ZZ Rejection Co") == 1000.0


@pytest.mark.asyncio
async def test_cfo_concentration_excludes_rejected(realdb):
    await _seed_vendor_invoices(realdb)
    async with realdb.client(key=TENANT, role="cfo") as c:
        body = (await c.get("/api/analytics/cfo")).json()
    conc = body["supplier_concentration"]
    # The concentration denominator counts only the approved spend.
    assert conc["largest_vendor"] == "ZZ Rejection Co"
    assert Decimal(str(conc["total_spend"])) == Decimal("1000")
