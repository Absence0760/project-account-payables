"""Rejected invoices are excluded from spend aggregates consistently.

The headline CFO `total_spend` already excluded rejected invoices, but the
dashboard's vendor-spend and the CFO supplier-concentration denominator did
not — so the same response disagreed with itself (a rejected invoice inflated
vendor spend / understated concentration shares). These realdb tests pin the
consistent exclusion across both surfaces.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

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


# ---------------------------------------------------------------------------
# Issue #126 — the CFO concentration tile (above) excluded rejected invoices,
# but its own drill-through and the vendor_spend export/scheduled report did
# NOT — so clicking from the tile into its drill-through, or exporting the
# same figure, disagreed with the number the CFO started from.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concentration_drill_through_excludes_rejected(realdb):
    await _seed_vendor_invoices(realdb)
    async with realdb.client(key=TENANT, role="cfo") as c:
        body = (await c.get("/api/analytics/drill/spend_concentration")).json()
    row = next(r for r in body["rows"] if r["vendor"] == "ZZ Rejection Co")
    # Only the approved $1000 counts — matches the tile, not 10000 (1000+9000).
    assert Decimal(str(row["amount"])) == Decimal("1000")
    assert row["invoice_count"] == 1


@pytest.mark.asyncio
async def test_vendor_spend_export_excludes_rejected(realdb):
    await _seed_vendor_invoices(realdb)
    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.get("/api/analytics/export/vendor_spend")
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("vendor_name"))
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    row = next(r for r in rows if r["vendor_name"] == "ZZ Rejection Co")
    assert Decimal(row["total_amount"]) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_scheduled_report_vendor_spend_excludes_rejected(realdb):
    from app.services.scheduled_reports import _generate_report_payload

    await _seed_vendor_invoices(realdb)
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    schedule = SimpleNamespace(report_type="vendor_spend", period_days=30, organization_id=org_id)
    async with mk() as s:
        csv_text = await _generate_report_payload(s, schedule)

    rows = list(csv.DictReader(io.StringIO(csv_text)))
    row = next(r for r in rows if r["vendor_name"] == "ZZ Rejection Co")
    assert Decimal(row["total_amount"]) == Decimal("1000.00")


# ---------------------------------------------------------------------------
# Issue #127 — a vendor billing in more than one currency was summed with a
# naive SQL SUM(amount), silently adding face values across currencies (e.g.
# a $1000 USD invoice + a €1000 invoice reported as "2000" instead of the
# correctly-converted USD total). This vendor has one USD invoice and one EUR
# invoice with a pre-materialized (locked) USD reporting amount; the naive
# bug would report 2000.00 on both the dashboard and the CFO concentration
# tile — the fix must report the converted total instead.
# ---------------------------------------------------------------------------


async def _seed_multi_currency_vendor(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = date.today()
    async with mk() as s:
        ent = await _default_entity_id(s)
        s.add(
            Invoice(
                organization_id=org_id,
                entity_id=ent,
                invoice_number="FX-USD-1",
                vendor_name="Global Supply Co",
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
                invoice_number="FX-EUR-1",
                vendor_name="Global Supply Co",
                amount=Decimal("1000.00"),
                currency="EUR",
                # Locked at extraction/materialization time — not the naive
                # face value the old SUM(amount) bug would have added in.
                reporting_currency="USD",
                reporting_amount=Decimal("1086.96"),
                status=InvoiceStatus.approved,
                invoice_date=today,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_dashboard_vendor_spend_converts_mixed_currency_invoices(realdb):
    await _seed_multi_currency_vendor(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()
    by_vendor = {v["vendor"]: v["amount"] for v in body["vendor_spend"]}
    # Correctly converted total (1000.00 + 1086.96), not the naive face-value
    # sum of 2000.00 a SUM(amount) across currencies would produce.
    assert by_vendor.get("Global Supply Co") == 2086.96


@pytest.mark.asyncio
async def test_cfo_concentration_converts_mixed_currency_invoices(realdb):
    await _seed_multi_currency_vendor(realdb)
    async with realdb.client(key=TENANT, role="cfo") as c:
        body = (await c.get("/api/analytics/cfo")).json()
    conc = body["supplier_concentration"]
    assert conc["largest_vendor"] == "Global Supply Co"
    assert Decimal(str(conc["total_spend"])) == Decimal("2086.96")
