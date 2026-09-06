"""`GET /api/cards/rebates` and `GET /api/inspections` — paginated, and honest.

Two lists that returned EVERYTHING and both back a table that only ever grows:
one rebate per settled virtual card, one row per inspection recorded or synced,
forever. Neither had a page, so the response size was a function of how long the
tenant had been running.

Paginating a list beside a money figure is where this repo's recurring defect
family lives (`docs/decisions.md` §79, §82): the moment the table shows a PAGE,
a rollup computed from the loaded rows stops describing the set it is captioned
against. So these tests pin the two things together — that the page is a page,
and that `total` / `total_amount` still span the whole filtered set.

They also pin the other half of the round-22 follow-up: a rebate now states the
currency it is denominated in. `card_rebates` has no currency column, so the
value is resolved from the `virtual_cards` row it accrued on — which is the only
thing that makes a MIXED-currency programme renderable at all. Before this the
rows had to be drawn as bare figures with no code.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.models.invoice import Invoice, InvoiceStatus
from app.models.procurement import GoodsReceipt
from app.models.quality_inspection import QualityInspection
from app.models.virtual_card import CardRebate, VirtualCard

TENANT = "a"


async def _seed_rebate(mk, org_id, *, currency: str, amount: str, period: str = "2026-06"):
    """A `VirtualCard` denominated in `currency` plus the rebate it earned."""
    async with mk() as s:
        inv = Invoice(
            invoice_number=f"PAGE-{uuid.uuid4().hex[:8]}",
            vendor_name="V",
            amount=Decimal(amount),
            currency=currency,
            status=InvoiceStatus.paid,
            organization_id=org_id,
            correlation_id=uuid.uuid4(),
        )
        s.add(inv)
        await s.flush()
        card = VirtualCard(
            invoice_id=inv.id,
            organization_id=org_id,
            card_provider="mock",
            provider_card_id=f"card_{uuid.uuid4().hex[:10]}",
            amount_limit=Decimal(amount),
            status="completed",
            currency=currency,
        )
        s.add(card)
        await s.flush()
        s.add(
            CardRebate(
                virtual_card_id=card.id,
                organization_id=org_id,
                amount=Decimal(amount),
                rate=Decimal("0.0100"),
                status="pending",
                period=period,
            )
        )
        await s.commit()
        return card.id


# ---------------------------------------------------------------------------
# GET /api/cards/rebates
# ---------------------------------------------------------------------------


async def test_rebate_rows_carry_the_currency_of_the_card_that_earned_them(realdb):
    """The follow-up round 22 opened: `RebateResponse` carried no currency.

    A rebate's denomination is knowable only through its card, so on a
    mixed-currency programme the UI could render nothing but bare figures. Each
    row now states its own code — and the row in the reporting currency and the
    row outside it are BOTH labelled, which is the whole point: the honest
    rendering used to be to label neither.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _seed_rebate(mk, org_id, currency="USD", amount="4.00")
    await _seed_rebate(mk, org_id, currency="EUR", amount="6.00")

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.get("/api/cards/rebates")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    by_amount = {str(r["amount"]): r["currency"] for r in body["items"]}
    assert by_amount == {"4.0": "USD", "6.0": "EUR"}
    # The total still narrows to one currency and still says what it left out.
    assert Decimal(str(body["total_amount"])) == Decimal("4.00")
    assert body["currency"] == "USD"
    assert body["excluded_rebate_count"] == 1


async def test_a_rebate_row_currency_is_normalised_by_the_one_owner(realdb):
    """`virtual_cards.currency` is a free-form `varchar(3)`; the code the API
    reports goes through `currency_conversion.card_currency_sql`, which uppers
    it. A row reported as `eur` would not match the `EUR` the formatter is
    handed, so the label and the figure would disagree on a technicality."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _seed_rebate(mk, org_id, currency="eur", amount="3.00")

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.get("/api/cards/rebates")
    assert [r["currency"] for r in resp.json()["items"]] == ["EUR"]


async def test_the_rebate_lifecycle_routes_also_state_a_currency(realdb):
    """`confirm` / `mark-paid` return the same `RebateResponse` the list does.

    A shape whose currency only appears on the list path is a shape the UI
    cannot trust after a transition — the row it swaps in would lose its code.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _seed_rebate(mk, org_id, currency="EUR", amount="8.00")

    async with realdb.client(key=TENANT, role="admin") as c:
        rebate_id = (await c.get("/api/cards/rebates")).json()["items"][0]["id"]
        confirmed = await c.post(f"/api/cards/rebates/{rebate_id}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["currency"] == "EUR"

        paid = await c.post(f"/api/cards/rebates/{rebate_id}/mark-paid")
        assert paid.status_code == 200, paid.text
        assert paid.json()["currency"] == "EUR"


async def test_the_rebate_list_is_paginated(realdb):
    """A page of rows, and a `total` that counts the whole set behind it."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    for _ in range(5):
        await _seed_rebate(mk, org_id, currency="USD", amount="2.00")

    async with realdb.client(key=TENANT, role="admin") as c:
        first = await c.get("/api/cards/rebates?page=1&page_size=2")
        assert first.status_code == 200, first.text
        body = first.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert (body["page"], body["page_size"]) == (1, 2)

        second = await c.get("/api/cards/rebates?page=2&page_size=2")
        third = await c.get("/api/cards/rebates?page=3&page_size=2")

    # Every row is served exactly once across the pages — the id tiebreak on the
    # ordering is what makes that true when `created_at` ties.
    seen = [r["id"] for page in (first, second, third) for r in page.json()["items"]]
    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_the_rebate_money_total_describes_the_whole_set_not_the_page(realdb):
    """The defect family this repo keeps re-finding: a figure captioning a set
    it does not describe. `total_amount` sits under a table that now shows one
    page, so it must be the sum of ALL matching rebates, not the two on screen.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    for _ in range(5):
        await _seed_rebate(mk, org_id, currency="USD", amount="2.00")

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.get("/api/cards/rebates?page=1&page_size=2")
    body = resp.json()
    assert len(body["items"]) == 2
    assert Decimal(str(body["total_amount"])) == Decimal("10.00")  # 5 × 2.00, not 4.00


async def test_the_rebate_money_total_follows_the_period_filter(realdb):
    """Whole-set means the whole FILTERED set. Both the rows and the total go
    through `_rebate_list_filters`, so a `period` narrows them together."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _seed_rebate(mk, org_id, currency="USD", amount="7.00", period="2026-01")
    await _seed_rebate(mk, org_id, currency="USD", amount="9.00", period="2026-02")

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.get("/api/cards/rebates?period=2026-01")
    body = resp.json()
    assert body["total"] == 1
    assert Decimal(str(body["total_amount"])) == Decimal("7.00")


async def test_the_rebate_page_size_is_bounded(realdb):
    """The shared `pagination_params` cap applies here like everywhere else — an
    unbounded `page_size` would hand back the whole table the pagination exists
    to stop."""
    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.get("/api/cards/rebates?page_size=5000")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/inspections
# ---------------------------------------------------------------------------


async def _seed_receipt(mk, org_id, *, gr_number: str) -> uuid.UUID:
    async with mk() as s:
        gr = GoodsReceipt(
            gr_number=gr_number,
            organization_id=org_id,
            received_date=datetime.now(UTC).date(),
        )
        s.add(gr)
        await s.commit()
        return gr.id


async def _seed_inspection(mk, org_id, *, number: str, gr_id: uuid.UUID | None):
    async with mk() as s:
        s.add(
            QualityInspection(
                inspection_number=number,
                gr_id=gr_id,
                result="pass",
                organization_id=org_id,
            )
        )
        await s.commit()


async def test_the_inspection_list_is_paginated(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    for i in range(5):
        await _seed_inspection(mk, org_id, number=f"QI-PAGE-{i}", gr_id=None)

    async with realdb.client(key=TENANT, role="admin") as c:
        first = await c.get("/api/inspections?page=1&page_size=2")
        assert first.status_code == 200, first.text
        body = first.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert (body["page"], body["page_size"]) == (1, 2)

        second = await c.get("/api/inspections?page=2&page_size=2")
        third = await c.get("/api/inspections?page=3&page_size=2")

    seen = [r["id"] for page in (first, second, third) for r in page.json()["items"]]
    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_an_inspection_row_names_its_goods_receipt(realdb):
    """`gr_id` with no `gr_number` is why `/goods-receipts` fetched a 100-row
    page of receipts on every inspection load, purely to label one column — and
    any receipt outside that window still rendered unlabelled. The join answers
    it for every row, in the response the UI already asks for."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    gr_id = await _seed_receipt(mk, org_id, gr_number="GR-LABELLED")
    await _seed_inspection(mk, org_id, number="QI-LABELLED", gr_id=gr_id)
    await _seed_inspection(mk, org_id, number="QI-UNLINKED", gr_id=None)

    async with realdb.client(key=TENANT, role="admin") as c:
        rows = {
            r["inspection_number"]: r for r in (await c.get("/api/inspections")).json()["items"]
        }

    assert rows["QI-LABELLED"]["gr_number"] == "GR-LABELLED"
    assert rows["QI-LABELLED"]["gr_id"] == str(gr_id)
    # An inspection tied to no receipt keeps a null, not an empty string — the
    # UI renders "not linked" from it, which is a different fact from "linked to
    # a receipt we could not name".
    assert rows["QI-UNLINKED"]["gr_number"] is None


async def test_the_inspection_detail_and_create_name_the_receipt_too(realdb):
    """Same shape on every path. A `gr_number` that only appears on the list is
    a blank cell everywhere else that fills itself in on the next reload."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    gr_id = await _seed_receipt(mk, org_id, gr_number="GR-SHAPE")

    async with realdb.client(key=TENANT, role="admin") as c:
        created = await c.post(
            "/api/inspections",
            json={"inspection_number": "QI-SHAPE", "gr_id": str(gr_id), "result": "pass"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["gr_number"] == "GR-SHAPE"

        detail = await c.get(f"/api/inspections/{created.json()['id']}")
        assert detail.status_code == 200
        assert detail.json()["gr_number"] == "GR-SHAPE"


async def test_the_inspection_list_filters_by_goods_receipt(realdb):
    """`?gr_id=` is what the receipt detail modal wants: it used to load every
    inspection in the tenant and filter it in the browser, because the server
    offered no way to ask. `total` narrows with the rows."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    mine = await _seed_receipt(mk, org_id, gr_number="GR-MINE")
    other = await _seed_receipt(mk, org_id, gr_number="GR-OTHER")
    await _seed_inspection(mk, org_id, number="QI-MINE-1", gr_id=mine)
    await _seed_inspection(mk, org_id, number="QI-MINE-2", gr_id=mine)
    await _seed_inspection(mk, org_id, number="QI-OTHER", gr_id=other)
    await _seed_inspection(mk, org_id, number="QI-NONE", gr_id=None)

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.get(f"/api/inspections?gr_id={mine}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {r["inspection_number"] for r in body["items"]} == {"QI-MINE-1", "QI-MINE-2"}
    assert body["total"] == 2


async def test_an_unknown_goods_receipt_filter_is_an_empty_page_not_an_error(realdb):
    """A receipt id that matches nothing is a legitimate empty result, not a
    404 — the caller asked a question with the answer "none"."""
    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.get(f"/api/inspections?gr_id={uuid.uuid4()}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


async def test_the_inspection_page_size_is_bounded(realdb):
    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.get("/api/inspections?page_size=5000")
    assert resp.status_code == 422
