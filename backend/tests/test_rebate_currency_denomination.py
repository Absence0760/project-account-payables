"""Every rebate rollup states the currency it is denominated in.

`card_rebates` carries **no currency column**. A rebate's denomination is only
knowable through the `virtual_cards` row it accrued on — which is why every
rebate rollup joins that table, and why five of them shipped as bare
cross-currency `SUM(CardRebate.amount)`s:

  * `GET /api/dashboard` — the "Rebates Earned" KPI on the main page
  * `GET /api/payments/summary` — `total_rebates`
  * `GET /api/cards/dashboard` — the rebate cards + YTD breakdown
  * `GET /api/cards/rebates` — the list's own `total`
  * `GET /api/analytics/cfo` — the rebate-yield NUMERATOR, divided by a
    reporting-currency denominator and then annualised

Each was a quantity in no currency at all, and several shipped under a response
that declared one two keys away. `currency_conversion.card_currency_sql` is
now the single owner of the expression.

This file guards the class, not just the instances: a source scan (so a SIXTH
rollup cannot be added bare) plus behavioural cover for the surfaces whose
figures a person reads.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.virtual_card import CardRebate, VirtualCard

TENANT = "a"
_APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


# ---------------------------------------------------------------------------
# The class guard — a new rollup cannot be added bare
# ---------------------------------------------------------------------------


def _statements_summing_rebate_amount() -> list[tuple[str, int, str]]:
    """Every statement under `app/` that sums `CardRebate.amount`.

    Statement-granular rather than call-granular: the currency predicate sits
    in a sibling `case(...)` or a `group_by(...)`, not inside the `func.sum`
    call itself, so the unit that must show one is the whole statement.
    """
    found: list[tuple[str, int, str]] = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - would fail the lint gate first
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt):
                continue
            if any(isinstance(c, ast.stmt) for c in ast.iter_child_nodes(node)):
                continue  # compound; its leaves are visited instead
            rendered = ast.unparse(node)
            if "CardRebate.amount" not in rendered:
                continue
            if "sum" not in rendered:
                continue
            found.append((str(path.relative_to(_APP_DIR.parent)), node.lineno, rendered))
    return found


def test_the_scan_still_finds_the_known_rollups():
    """Guards the guard. The assertion below is a loop over whatever this scan
    returns, so a scan that silently stops matching (a renamed column, a moved
    package root) would pass by finding nothing."""
    found = _statements_summing_rebate_amount()
    assert found, "the scan matched no rebate sums at all — it has stopped working"


@pytest.mark.parametrize(
    "site", _statements_summing_rebate_amount(), ids=lambda s: f"{s[0]}:{s[1]}"
)
def test_every_rebate_sum_is_denominated(site):
    """A rebate sum must either FILTER to one currency or GROUP BY currency.

    Filtering is what an endpoint reporting a single figure does (and it counts
    the excluded rows so the figure says it is partial); grouping is what the
    billing meter does, because a meter a later slice prices cannot be a mixed
    scalar. Either is denominated. Neither is a cross-currency sum.
    """
    path, lineno, rendered = site
    denominated = (
        "card_currency_sql" in rendered
        or "_rebate_ccy" in rendered
        or "_card_ccy" in rendered
        or "group_by" in rendered
        or "rebate_currency" in rendered
    )
    assert denominated, (
        f"{path}:{lineno} sums CardRebate.amount without stating a currency. "
        "`card_rebates` has no currency column, so join `virtual_cards` and use "
        "`currency_conversion.card_currency_sql` — either filtering to the "
        "reporting currency (and counting what that excluded) or grouping by it."
    )


def test_the_shared_expression_has_one_owner():
    """Five call sites spelled this themselves. If a sixth re-derives
    `func.upper(func.coalesce(VirtualCard.currency, ...))` inline, they can
    drift on the normalisation — and an un-uppercased comparison excludes every
    row rather than failing loudly."""
    inline = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        if path.name == "currency_conversion.py":
            continue  # the owner
        source = path.read_text()
        if "coalesce(VirtualCard.currency" in source:
            inline.append(str(path.relative_to(_APP_DIR.parent)))
    assert not inline, (
        f"{inline} re-derive the rebate-currency expression inline; call "
        "`currency_conversion.card_currency_sql` instead."
    )


# ---------------------------------------------------------------------------
# Behavioural — the figures a person actually reads
# ---------------------------------------------------------------------------


def _org(org_id, *, reporting_currency="USD"):
    return SimpleNamespace(
        id=org_id,
        name="PyTest",
        slug="pytesta",
        settings={"reporting_currency": reporting_currency},
    )


def _user():
    return SimpleNamespace(id=uuid.uuid4(), full_name="Rebate Tester", roles=["admin"])


async def _seed_rebate(mk, org_id, *, currency: str, amount: str, entity_id=None) -> uuid.UUID:
    """A `VirtualCard` in `currency` plus its `CardRebate`."""
    async with mk() as s:
        inv = Invoice(
            id=uuid.uuid4(),
            invoice_number=f"REB-{uuid.uuid4().hex[:8]}",
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
            id=uuid.uuid4(),
            invoice_id=inv.id,
            organization_id=org_id,
            card_provider="mock",
            provider_card_id=f"card_{uuid.uuid4().hex[:10]}",
            amount_limit=Decimal(amount),
            status="active",
            currency=currency,
        )
        if entity_id is not None:
            card.entity_id = entity_id
        s.add(card)
        await s.flush()
        s.add(
            CardRebate(
                virtual_card_id=card.id,
                organization_id=org_id,
                amount=Decimal(amount),
                rate=Decimal("0.0100"),
                status="confirmed",
                period=datetime.now(UTC).strftime("%Y-%m"),
            )
        )
        await s.commit()
        return card.id


async def test_the_dashboard_rebate_kpi_reports_one_currency(realdb):
    """ "Rebates Earned" on the main page — the rebate figure most people read.

    Pre-fix this added USD 10 + EUR 7 + GBP 5 into a single "22" rendered with
    the org's currency symbol.
    """
    from app.api.dashboard import get_dashboard

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _seed_rebate(mk, org_id, currency="USD", amount="10.00")
    await _seed_rebate(mk, org_id, currency="EUR", amount="7.00")
    await _seed_rebate(mk, org_id, currency="GBP", amount="5.00")

    async with mk() as s:
        res = await get_dashboard(db=s, org=_org(org_id), user=_user(), entity_id=None)

    assert Decimal(str(res["total_rebates"])) == Decimal("10.00")
    assert res["excluded_rebate_count"] == 2


async def test_the_dashboard_rebate_kpi_is_entity_scoped(realdb):
    """It already joined `VirtualCard` for the entity scope — the currency
    filter must not have disturbed that."""
    from app.api.dashboard import get_dashboard

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        other = Entity(organization_id=org_id, name="Sub B", slug=f"sub-{uuid.uuid4().hex[:6]}")
        s.add(other)
        await s.commit()
        other_id = other.id
        default_id = (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()

    await _seed_rebate(mk, org_id, currency="USD", amount="9.00", entity_id=other_id)
    await _seed_rebate(mk, org_id, currency="USD", amount="1.00", entity_id=default_id)

    async with mk() as s:
        scoped = await get_dashboard(db=s, org=_org(org_id), user=_user(), entity_id=default_id)
    assert Decimal(str(scoped["total_rebates"])) == Decimal("1.00")


async def test_the_rebate_list_total_matches_the_rows_it_sits_above(realdb):
    """`GET /api/cards/rebates` renders per-row amounts that each state their
    own card's currency, under one `total`. A cross-currency total is therefore
    denominated in nothing the reader can see on the page."""
    from app.api.cards import list_rebates

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _seed_rebate(mk, org_id, currency="USD", amount="4.00")
    await _seed_rebate(mk, org_id, currency="EUR", amount="6.00")

    async with mk() as s:
        res = await list_rebates(period=None, db=s, org=_org(org_id), user=_user(), entity_id=None)

    assert Decimal(str(res.total)) == Decimal("4.00")
    assert res.currency == "USD"
    assert res.excluded_rebate_count == 1
    # Both rows are still listed — the total narrows, the list does not.
    assert len(res.items) == 2


async def test_the_rebate_yield_numerator_shares_its_denominators_currency(realdb):
    """The sharpest of the five: the numerator is divided by a
    reporting-currency `total_spend` and the ratio is then annualised, so a
    cross-currency numerator makes the yield a ratio between two different
    units."""
    from app.api.analytics import get_cfo_analytics

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _seed_rebate(mk, org_id, currency="USD", amount="10.00")
    await _seed_rebate(mk, org_id, currency="EUR", amount="90.00")

    async with mk() as s:
        res = await get_cfo_analytics(
            period_days=90, db=s, org=_org(org_id), user=_user(), entity_id=None
        )

    # 10.00, not 100.00 — the EUR rebate is not this denominator's currency.
    assert Decimal(str(res["rebate_yield"]["rebates_total"])) == Decimal("10.00")
