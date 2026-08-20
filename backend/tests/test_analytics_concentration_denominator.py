"""Supplier concentration is measured against the WHOLE period's spend.

``compute_supplier_concentration`` derives its denominator from the list it is
handed and takes its own ``[:10]`` / ``[:50]`` cuts, so a caller that slices
first silently rebases every share onto that slice. Two call sites did:

* ``GET /api/analytics/cfo`` passed ``vendor_entries[:50]``, which made
  ``total_spend`` the top-50 subtotal, inflated ``top_10_share_pct`` /
  ``largest_vendor_share_pct`` (and with them the ``flagged`` risk warning),
  and pinned ``top_50_share_pct`` at exactly ``100.0`` on any tenant with 50 or
  more vendors — a metric that could not carry information.
* ``GET /api/analytics/drill/spend_concentration`` summed **after** applying
  ``?limit=``, so ``total_spend`` was a per-page tally labelled as the whole-set
  total and ``share_pct`` moved whenever the caller changed ``limit`` — the
  drill-through disagreeing with the tile it was opened from, the same failure
  shape as issue #126.

DO NOT run this file standalone in a concurrent build — the ``realdb`` fixture
truncates all tables sequentially. The orchestrator runs the suite at the end.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus

TENANT = "a"

#: Deliberately > 50 so the top-50 cut is a real cut.
_VENDOR_COUNT = 60
_PER_VENDOR = Decimal("1000.00")
_TOTAL = _PER_VENDOR * _VENDOR_COUNT  # 60_000.00


async def _seed_many_vendors(realdb) -> None:
    """One approved $1,000 invoice for each of 60 distinct vendors."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = date.today()
    async with mk() as s:
        ent = (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()
        for i in range(_VENDOR_COUNT):
            s.add(
                Invoice(
                    organization_id=org_id,
                    entity_id=ent,
                    invoice_number=f"CONC-{i:03d}",
                    vendor_name=f"Concentration Vendor {i:03d}",
                    amount=_PER_VENDOR,
                    currency="USD",
                    status=InvoiceStatus.approved,
                    invoice_date=today,
                )
            )
        await s.commit()


@pytest.mark.asyncio
async def test_cfo_concentration_denominator_is_the_whole_vendor_set(realdb):
    await _seed_many_vendors(realdb)
    async with realdb.client(key=TENANT, role="cfo") as c:
        conc = (await c.get("/api/analytics/cfo")).json()["supplier_concentration"]

    # The denominator is every vendor's spend, not the top 50's subtotal
    # (which would read 50000.00).
    assert Decimal(str(conc["total_spend"])) == _TOTAL

    # 50 of 60 equal vendors → 83.3%. Pre-fix this was 100.0 by construction:
    # the caller had already cut the list to 50, so the top-50 slice WAS the
    # whole denominator and the metric could never say anything.
    assert conc["top_50_share_pct"] == pytest.approx(83.3)
    assert conc["top_50_share_pct"] < 100.0

    # 10 of 60 → 16.7 (pre-fix 20.0, measured against the top-50 subtotal).
    assert conc["top_10_share_pct"] == pytest.approx(16.7)

    # 1 of 60 → 1.7 (pre-fix 2.0). This is the number `flagged` is derived
    # from, so an inflated share also inflates the concentration-risk warning.
    assert conc["largest_vendor_share_pct"] == pytest.approx(1.7)
    assert conc["flagged"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [10, 50, 200])
async def test_concentration_drill_total_and_shares_are_limit_independent(realdb, limit):
    """`?limit=` selects how many rows come back — not what they are shares OF."""
    await _seed_many_vendors(realdb)
    async with realdb.client(key=TENANT, role="cfo") as c:
        body = (await c.get(f"/api/analytics/drill/spend_concentration?limit={limit}")).json()

    assert len(body["rows"]) == min(limit, _VENDOR_COUNT)
    # Whole-set total regardless of the page size (pre-fix: 10000 / 50000 / 60000).
    assert Decimal(str(body["total_spend"])) == _TOTAL
    # Every vendor is 1/60th of spend whatever the page size
    # (pre-fix at limit=10 this read 10.0 — five times the tile's own figure).
    assert body["rows"][0]["share_pct"] == pytest.approx(1.7)


@pytest.mark.asyncio
async def test_drill_total_reconciles_with_the_tile_it_drills_into(realdb):
    await _seed_many_vendors(realdb)
    async with realdb.client(key=TENANT, role="cfo") as c:
        tile = (await c.get("/api/analytics/cfo")).json()["supplier_concentration"]
        drill = (await c.get("/api/analytics/drill/spend_concentration?limit=10")).json()

    assert Decimal(str(drill["total_spend"])) == Decimal(str(tile["total_spend"]))
    assert drill["rows"][0]["share_pct"] == pytest.approx(tile["largest_vendor_share_pct"])
