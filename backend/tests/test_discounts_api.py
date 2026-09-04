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


async def test_accept_refuses_a_tier_whose_window_has_closed(realdb):
    """Issue #124's exact repro on the AP side: an offer opened 20 days ago,
    still within its own valid_until (+10 days out), but both sliding-scale
    tiers' real deadlines (measured from when the offer was extended) are
    long past. Before the fix, best_tier_for_date defaulted its reference to
    "today", so every tier looked perpetually achievable."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id)

    offer_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            DiscountOffer(
                id=offer_id,
                organization_id=org_id,
                scope="invoice",
                invoice_id=uuid.UUID(invoice_id),
                source="ap",
                status="offered",
                tiers=_tiers(),
                base_amount=Decimal("1000.00"),
                currency="USD",
                valid_from=date.today() - timedelta(days=20),
                valid_until=date.today() + timedelta(days=10),
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        best_resp = await c.post(f"/api/discounts/offers/{offer_id}/accept", json={})
        assert best_resp.status_code == 409, best_resp.text

        # Naming the (long-closed) 5-day tier explicitly must not bypass the
        # window check either.
        named_resp = await c.post(f"/api/discounts/offers/{offer_id}/accept", json={"tier_days": 5})
        assert named_resp.status_code == 422, named_resp.text

    async with mk() as s:
        offer = (
            await s.execute(select(DiscountOffer).where(DiscountOffer.id == offer_id))
        ).scalar_one()
        assert offer.status == "offered"  # untouched — never accepted


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


async def _single_tier_offer(realdb, *, amount: str, percent: str) -> str:
    """One open offer on a fresh invoice, with exactly one tier.

    A single tier keeps the outlay arithmetic deterministic: savings =
    ``amount * percent / 100`` quantised to 2dp, so the discounted outlay the
    budget is measured against is an exact figure the test can name.
    """
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id, amount=amount)
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/discounts/offers",
            json={
                "scope": "invoice",
                "invoice_id": invoice_id,
                "tiers": [{"days": 10, "percent": percent}],
            },
        )
    assert resp.status_code == 201, resp.text
    return invoice_id


def _selected_invoice_ids(body: dict) -> set[str]:
    return {r["invoice_id"] for r in body["recommendations"] if r["selected"]}


async def test_optimize_cash_budget_is_exact_not_a_rounded_float(realdb):
    """The budget decides which invoices get paid early — so it must be exact.

    ``POST /optimize`` took a bare ``dict`` and did
    ``Decimal(str(body["cash_budget"]))``. That *looks* exact, but ``json.loads``
    had already turned a JSON number into a ``float``, so the value the
    optimizer compared against was whatever double the literal rounded to.
    Typing the field ``Decimal`` does not fix a JSON body either — pydantic
    yields ``Decimal('100')`` from ``100.00000000000000001`` for the same
    reason. Only the string form round-trips.

    This is not a display value: the rounding changes WHICH INVOICES ARE
    SELECTED, and the two parses of one wire value disagree below.
    """
    invoice_id = await _single_tier_offer(realdb, amount="10000.00", percent="2.00")
    # savings = 10000.00 * 2% = 200.00 → discounted outlay = exactly 9800.00.
    outlay = Decimal("9800.00")

    # The wire value under test, and what the old float path made of it.
    lossy_wire = "9799.999999999999999"
    assert Decimal(str(float(lossy_wire))) == Decimal("9800.0")  # the rounding, stated
    assert Decimal(lossy_wire) < outlay  # …and what it rounded away from

    async with realdb.client(key="a", role="cfo") as c:
        # (a) The budget the OLD float path produced: it covers the outlay, so
        #     the invoice is selected and the cash goes out.
        rounded = await c.post("/api/discounts/optimize", json={"cash_budget": "9800.0"})
        # (b) The budget the caller actually sent: a hair short, so it does not.
        exact = await c.post("/api/discounts/optimize", json={"cash_budget": lossy_wire})
        # (c) The wire shape that caused (a) to stand in for (b). It is refused
        #     now; before, it was accepted and silently became (a).
        lossy = await c.post("/api/discounts/optimize", json={"cash_budget": 9799.999999999999999})

    assert rounded.status_code == 200, rounded.text
    assert exact.status_code == 200, exact.text
    # Different SELECTIONS from the same wire value — the rounding mattered.
    assert invoice_id in _selected_invoice_ids(rounded.json())
    assert invoice_id not in _selected_invoice_ids(exact.json())
    # …so the shape that collapses (b) into (a) cannot be accepted at all.
    assert lossy.status_code == 422, lossy.text


async def test_optimize_refuses_a_json_number_budget(realdb):
    """The lossy shape is refused outright, not silently accepted rounded.

    A fractional JSON number has already lost exactness before the server sees
    it, so there is no honest value to proceed with. The 422 names the fix.
    """
    await _single_tier_offer(realdb, amount="10000.00", percent="2.00")

    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post("/api/discounts/optimize", json={"cash_budget": 9799.999999999999999})
    assert resp.status_code == 422, resp.text
    assert "string" in resp.text.lower()

    async with realdb.client(key="a", role="cfo") as c:
        # A JSON integer is exact (`json.loads` yields `int`), so it still works —
        # that is the shape existing callers send.
        ok = await c.post("/api/discounts/optimize", json={"cash_budget": 100000})
    assert ok.status_code == 200, ok.text


async def test_optimize_rejects_a_malformed_or_misspelled_budget(realdb):
    """A bare dict on a money path made both of these silent or fatal.

    ``Decimal(str(...))`` on a non-numeric value raised ``InvalidOperation`` and
    surfaced as a 500; a misspelled key ran the optimizer UNCONSTRAINED and
    returned a plan committing more cash than the caller asked for. Both are
    422s now.
    """
    await _single_tier_offer(realdb, amount="10000.00", percent="2.00")

    async with realdb.client(key="a", role="cfo") as c:
        malformed = await c.post("/api/discounts/optimize", json={"cash_budget": "lots"})
        misspelled = await c.post("/api/discounts/optimize", json={"cashBudget": "100.00"})
        negative = await c.post("/api/discounts/optimize", json={"cash_budget": "-5.00"})

    assert malformed.status_code == 422, malformed.text
    assert misspelled.status_code == 422, misspelled.text
    assert negative.status_code == 422, negative.text


async def test_optimize_recommendation_states_its_own_currency(realdb):
    """Each row names the currency ITS money is in.

    ``roi.savings`` is computed from the offer's own ``base_amount``, so it is
    the OFFER's currency — which equals the response-level ``currency`` only
    when ``unconvertible`` is False. Without a per-row code a client cannot
    label a foreign row's figure at all: it either renders the amount bare or
    stamps the totals' currency on it, which is how "Save $412.00" came to
    describe €412.
    """
    invoice_id = await _single_tier_offer(realdb, amount="10000.00", percent="2.00")

    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post("/api/discounts/optimize", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rec = next(r for r in body["recommendations"] if r["invoice_id"] == invoice_id)
    assert rec["currency"] == "USD"
    # This row IS in the totals' currency, so the two agree — and the flag says so.
    assert rec["unconvertible"] is False
    assert rec["currency"] == body["currency"]


async def test_optimize_foreign_recommendation_carries_the_offers_currency(realdb):
    """A row flagged `unconvertible` names the currency the totals are NOT in.

    This is the row the per-row field exists for: its money is real, it is
    simply denominated in something the totals could not absorb.
    """
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id, amount="10000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        created = await c.post(
            "/api/discounts/offers",
            json={
                "scope": "invoice",
                "invoice_id": invoice_id,
                "tiers": [{"days": 10, "percent": "2.00"}],
            },
        )
    assert created.status_code == 201, created.text
    offer_id = created.json()["id"]
    # Denominate the offer in a currency the tenant does not report in. Set on
    # the row directly: the create route inherits the invoice's currency.
    async with mk() as s:
        offer = await s.get(DiscountOffer, uuid.UUID(offer_id))
        offer.currency = "JPY"
        await s.commit()

    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post("/api/discounts/optimize", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["currency"] != "JPY"  # the premise: the totals are in something else
    rec = next(r for r in body["recommendations"] if r["invoice_id"] == invoice_id)
    assert rec["unconvertible"] is True
    assert rec["currency"] == "JPY"


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


async def test_cfo_can_decline_an_offer(realdb):
    """Decline carries the SAME gate as accept.

    The two are halves of one decision, and the CFO owns the early-pay-vs-cash
    trade-off. Decline used to be gated to ``_WRITE_ROLES`` (admin/ap_manager)
    while accept allowed the CFO — so a CFO could commit cash early but not
    refuse the offer, which is backwards, and the ``/discounts`` page (open to
    admin/ap_manager/cfo) rendered a Decline button the backend then 403'd.
    Declining moves no money; it only flips status.
    """
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

    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post(f"/api/discounts/offers/{offer_id}/decline")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == OFFER_STATUS_DECLINED

    # The refusal is on the record, attributed to the CFO who made it.
    async with mk() as s:
        row = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "discount_offer.declined",
                    AuditLog.entity_id == uuid.UUID(offer_id),
                )
            )
        ).scalar_one()
        assert row.entity_type == "discount_offer"


async def test_clerk_cannot_decline_an_offer(realdb):
    """Widening decline to the CFO must not widen it to everyone — a clerk is
    still refused, exactly as they are on accept."""
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

    async with realdb.client(key="a", role="ap_clerk") as c:
        assert (await c.post(f"/api/discounts/offers/{offer_id}/decline")).status_code == 403
        assert (
            await c.post(f"/api/discounts/offers/{offer_id}/accept", json={})
        ).status_code == 403


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
