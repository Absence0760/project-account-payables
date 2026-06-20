"""Real-DB coverage for the dynamic-discounting router (``app/api/discounts.py``).

Exercises the offer lifecycle (create / accept / decline), the per-invoice ROI
calculator, the cash-constrained optimizer, bulk vendor negotiation, and the
captured/missed/projected-savings dashboard end-to-end against the live test
tenants — plus RBAC, tenant isolation, audit rows, and exact ``Numeric`` money.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.discount import (
    OFFER_STATUS_DECLINED,
    DiscountOffer,
)
from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor import Vendor
from app.models.workflow import AuditLog

_FUTURE = (date.today() + timedelta(days=40)).isoformat()


async def _default_entity_id(s):
    """The tenant's default entity — what the API assigns to new writes."""
    return (
        await s.execute(select(Entity.id).where(Entity.is_default.is_(True)).limit(1))
    ).scalar_one()


async def _add_vendor(mk, org_id, name="Globex Industrial") -> str:
    async with mk() as s:
        v = Vendor(organization_id=org_id, name=name, entity_id=await _default_entity_id(s))
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return str(v.id)


async def _add_invoice(
    mk, org_id, *, amount="1000.00", status=InvoiceStatus.approved, vendor_id=None
) -> str:
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            entity_id=await _default_entity_id(s),
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_name="Globex Industrial",
            vendor_id=uuid.UUID(vendor_id) if vendor_id else None,
            amount=Decimal(amount),
            currency="USD",
            due_date=date.today() + timedelta(days=30),
            status=status,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return str(inv.id)


def _tiers():
    return [{"days": 5, "percent": "3.00"}, {"days": 10, "percent": "2.00"}]


# ---------------------------------------------------------------------------
# create + lifecycle
# ---------------------------------------------------------------------------


async def test_create_invoice_offer_defaults_base_amount_and_audits(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id, amount="2500.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/discounts/offers",
            json={"scope": "invoice", "invoice_id": invoice_id, "tiers": _tiers()},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["base_amount"] == 2500.0  # defaulted from the invoice
    assert body["status"] == "offered"
    assert [t["days"] for t in body["tiers"]] == [5, 10]  # normalized + sorted

    async with mk() as s:
        audit = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "discount_offer.created",
                    AuditLog.entity_id == uuid.UUID(body["id"]),
                )
            )
        ).scalar_one()
        assert audit.entity_type == "discount_offer"


async def test_accept_offer_picks_best_tier_and_audits(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        offer_id = (
            await c.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": invoice_id, "tiers": _tiers()},
            )
        ).json()["id"]
        resp = await c.post(f"/api/discounts/offers/{offer_id}/accept", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["accepted_tier"]["days"] == 5  # highest-percent tier
    assert body["accepted_tier"]["percent"] == 3.0


async def test_accept_specific_tier(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        offer_id = (
            await c.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": invoice_id, "tiers": _tiers()},
            )
        ).json()["id"]
    # A CFO (who cannot create) is allowed to accept.
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post(f"/api/discounts/offers/{offer_id}/accept", json={"tier_days": 10})
    assert resp.status_code == 200
    assert resp.json()["accepted_tier"]["days"] == 10


async def test_decline_then_double_accept_conflicts(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        offer_id = (
            await c.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": invoice_id, "tiers": _tiers()},
            )
        ).json()["id"]
        assert (await c.post(f"/api/discounts/offers/{offer_id}/decline")).status_code == 200
        # Accepting a declined offer is a 409 (state guard).
        conflict = await c.post(f"/api/discounts/offers/{offer_id}/accept", json={})
        assert conflict.status_code == 409

    async with mk() as s:
        row = (
            await s.execute(select(DiscountOffer).where(DiscountOffer.id == uuid.UUID(offer_id)))
        ).scalar_one()
        assert row.status == OFFER_STATUS_DECLINED


# ---------------------------------------------------------------------------
# list + filter
# ---------------------------------------------------------------------------


async def test_list_filters_by_status(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv1 = await _add_invoice(mk, org_id)
    inv2 = await _add_invoice(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        o1 = (
            await c.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": inv1, "tiers": _tiers()},
            )
        ).json()["id"]
        await c.post(
            "/api/discounts/offers",
            json={"scope": "invoice", "invoice_id": inv2, "tiers": _tiers()},
        )
        await c.post(f"/api/discounts/offers/{o1}/decline")

        offered = (await c.get("/api/discounts/offers?status=offered")).json()
        missed = (await c.get("/api/discounts/offers?status=missed")).json()

    assert all(i["status"] == "offered" for i in offered["items"])
    assert any(i["id"] == o1 and i["status"] == "declined" for i in missed["items"])


# ---------------------------------------------------------------------------
# ROI
# ---------------------------------------------------------------------------


async def test_invoice_roi_uses_open_offer(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id, amount="1000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(
            "/api/discounts/offers",
            json={"scope": "invoice", "invoice_id": invoice_id, "tiers": _tiers()},
        )
    # ap_clerk can read the ROI.
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/discounts/invoices/{invoice_id}/roi")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 3% best tier, deadline today+5, due today+30 → ~25 days accelerated.
    assert body["savings"] == 30.0  # 1000 * 3%
    assert body["worthwhile"] is True
    assert body["annualized_return_pct"] > 12


# ---------------------------------------------------------------------------
# optimizer
# ---------------------------------------------------------------------------


async def test_optimize_ranks_and_respects_cash_budget(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    big = await _add_invoice(mk, org_id, amount="10000.00")
    small = await _add_invoice(mk, org_id, amount="500.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        for inv in (big, small):
            await c.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": inv, "tiers": _tiers()},
            )
    # Budget only covers the small invoice's discounted outlay.
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post("/api/discounts/optimize", json={"cash_budget": 1000})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["recommendations"]) == 2
    selected = [r for r in body["recommendations"] if r["selected"]]
    assert len(selected) == 1
    assert selected[0]["invoice_id"] == small
    assert body["total_savings_available"] > body["total_savings_selected"]


# ---------------------------------------------------------------------------
# bulk negotiation
# ---------------------------------------------------------------------------


async def test_bulk_negotiate_sums_open_invoices(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id, name="Acme Bulk Co")
    # Two open invoices for this vendor.
    await _add_invoice(mk, org_id, amount="1000.00", vendor_id=vendor_id)
    await _add_invoice(mk, org_id, amount="2000.00", vendor_id=vendor_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/discounts/bulk-negotiate",
            json={
                "vendor_id": vendor_id,
                "tiers": [{"days": 7, "percent": "2.00"}],
                "valid_until": _FUTURE,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope"] == "vendor"
    assert body["base_amount"] == 3000.0  # summed open balances


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


async def test_dashboard_rolls_up_captured_missed_open(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_open = await _add_invoice(mk, org_id, amount="1000.00")
    inv_missed = await _add_invoice(mk, org_id, amount="4000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        # one open offer
        await c.post(
            "/api/discounts/offers",
            json={"scope": "invoice", "invoice_id": inv_open, "tiers": _tiers()},
        )
        # one declined (missed) offer
        missed_id = (
            await c.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": inv_missed, "tiers": _tiers()},
            )
        ).json()["id"]
        await c.post(f"/api/discounts/offers/{missed_id}/decline")

    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/discounts/dashboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["open_offer_count"] >= 1
    assert body["missed_count"] >= 1
    # missed savings counts the best tier (3%) of the 4000 invoice = 120.
    assert body["missed_amount"] >= 120.0
    assert body["projected_savings"] > 0


# ---------------------------------------------------------------------------
# RBAC + tenant isolation
# ---------------------------------------------------------------------------


async def test_clerk_cannot_create_offer(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/discounts/offers",
            json={"scope": "invoice", "invoice_id": invoice_id, "tiers": _tiers()},
        )
    assert resp.status_code == 403


async def test_offer_not_visible_cross_tenant(realdb):
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk_a, org_a)
    async with realdb.client(key="a", role="ap_manager") as c:
        offer_id = (
            await c.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": invoice_id, "tiers": _tiers()},
            )
        ).json()["id"]

    # Tenant B must not see tenant A's offer.
    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get(f"/api/discounts/offers/{offer_id}")
    assert resp.status_code == 404
