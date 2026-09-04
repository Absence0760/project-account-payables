"""Tests for the `card_dashboard` endpoint in `app/api/cards.py`.

We pin the four top-level counters, the pending/confirmed/paid_out status
breakdown, and the derived `projected_annual_rebates` calculation. The query
layer is mocked the same way as `test_payment_summary` — sequential
`execute()` calls feed scalar/row values back through `.one()` / `.scalar()`.

**Only `confirmed` + `paid_out` `CardRebate` rows are REALIZED money.** A
`pending` rebate is the processor's own estimate — not yet confirmed by its
out-of-band settlement (`POST /rebates/{id}/confirm`) and further still from
an actual payout (`/mark-paid`). Blending all three into one "Rebates Earned"
figure let 100% of a displayed "earned" total be entirely unconfirmed money
that might never materialize — this file pins the fix: the headline
`rebates_this_month` / `rebates_ytd` / `projected_annual_rebates` fields
report ONLY the realized (confirmed + paid_out) total, and the full
pending/confirmed/paid_out split rides alongside on
`rebates_this_month_by_status` / `rebates_ytd_by_status`.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_db_session(
    active_row,
    spend_scalar,
    rebate_month_row,
    rebate_ytd_row,
    *,
    excluded_cards=0,
    excluded_rebates=0,
):
    """Build a session that walks the endpoint's six queries in order:

    1. `active_q`            → `.one()`    → (active_count, active_value)
    2. excluded active cards → `.scalar()` → count in another currency
    3. `spend_q`             → `.scalar()` → spend_this_month
    4. `rebate_month_q`      → `.one()`    → (pending, confirmed, paid_out)
    5. `rebate_ytd_q`        → `.one()`    → (pending, confirmed, paid_out)
    6. excluded YTD rebates  → `.scalar()` → count earned on another currency

    The two `excluded_*` queries exist because every money field in the
    response is reported under ONE currency code, so each aggregate counts only
    rows denominated in it and says how many it left out.
    """
    session = AsyncMock()

    results = []

    active_result = MagicMock()
    active_result.one.return_value = active_row
    results.append(active_result)

    excluded_cards_result = MagicMock()
    excluded_cards_result.scalar.return_value = excluded_cards
    results.append(excluded_cards_result)

    spend_result = MagicMock()
    spend_result.scalar.return_value = spend_scalar
    results.append(spend_result)

    month_result = MagicMock()
    month_result.one.return_value = rebate_month_row
    results.append(month_result)

    ytd_result = MagicMock()
    ytd_result.one.return_value = rebate_ytd_row
    results.append(ytd_result)

    excluded_rebates_result = MagicMock()
    excluded_rebates_result.scalar.return_value = excluded_rebates
    results.append(excluded_rebates_result)

    async def execute_side_effect(_query):
        return results.pop(0)

    session.execute.side_effect = execute_side_effect
    return session


def _user():
    return SimpleNamespace(id="user-1", roles=["admin"])


def _org(settings=None):
    """The tenant org the endpoint resolves its reporting currency from."""
    return SimpleNamespace(id="org-1", settings=settings if settings is not None else {})


@pytest.mark.asyncio
async def test_card_dashboard_returns_all_fields():
    """All fields the frontend rebate panel reads must be present, and the
    headline totals must be REALIZED (confirmed + paid_out) only — the
    month's $75 is entirely `pending`, so the headline reports $0, not $75."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (3, Decimal("10000.00")),  # active_count, active_value
        Decimal("5000.00"),  # spend_this_month
        (Decimal("75.00"), Decimal("0"), Decimal("0")),  # month: pending/confirmed/paid_out
        (Decimal("400.00"), Decimal("0"), Decimal("0")),  # ytd: pending/confirmed/paid_out
    )

    result = await card_dashboard(db=db, org=_org(), user=_user())

    assert result.active_cards == 3
    assert result.active_cards_value == 10000.0
    assert result.spend_this_month == 5000.0
    # Entirely unconfirmed money — the headline must NOT report it as earned.
    assert result.rebates_this_month == 0.0
    assert result.rebates_ytd == 0.0
    assert result.projected_annual_rebates == 0.0
    assert result.rebates_this_month_by_status.pending_total == 75.0
    assert result.rebates_ytd_by_status.pending_total == 400.0


@pytest.mark.asyncio
async def test_realized_total_excludes_pending_and_splits_by_status():
    """The core regression: a mix of all three statuses. $50 pending, $30
    confirmed, $20 paid_out this month → headline realized = $50 (confirmed +
    paid_out), NOT $100 (the old blended sum)."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (0, Decimal("0")),
        Decimal("0"),
        (Decimal("50.00"), Decimal("30.00"), Decimal("20.00")),  # month
        (Decimal("500.00"), Decimal("300.00"), Decimal("200.00")),  # ytd
    )

    result = await card_dashboard(db=db, org=_org(), user=_user())

    # Headline = confirmed + paid_out only.
    assert result.rebates_this_month == pytest.approx(50.0)
    assert result.rebates_ytd == pytest.approx(500.0)

    month_status = result.rebates_this_month_by_status
    assert month_status.pending_total == pytest.approx(50.0)
    assert month_status.confirmed_total == pytest.approx(30.0)
    assert month_status.paid_out_total == pytest.approx(20.0)

    ytd_status = result.rebates_ytd_by_status
    assert ytd_status.pending_total == pytest.approx(500.0)
    assert ytd_status.confirmed_total == pytest.approx(300.0)
    assert ytd_status.paid_out_total == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_projected_annual_extrapolates_from_realized_ytd_when_present():
    """The headline `projected_annual` runs at REALIZED-YTD pace: 400
    confirmed+paid_out YTD in month 5 → 400/5 × 12 = 960 annualised — a large
    `pending` balance must not inflate it."""
    from app.api.cards import card_dashboard

    fixed_now = SimpleNamespace(month=5, year=2026, strftime=lambda fmt: "2026-05")

    db = _make_db_session(
        (0, Decimal("0")),
        Decimal("0"),
        # month row ignored when realized YTD is non-zero
        (Decimal("9999.00"), Decimal("40.00"), Decimal("40.00")),
        # ytd: pending 9999 (must be ignored), confirmed 250, paid_out 150 → realized 400
        (Decimal("9999.00"), Decimal("250.00"), Decimal("150.00")),
    )

    with patch("app.api.cards.datetime") as mk_dt:
        mk_dt.now.return_value = fixed_now
        result = await card_dashboard(db=db, org=_org(), user=_user())

    assert result.projected_annual_rebates == pytest.approx(960.0)


@pytest.mark.asyncio
async def test_projected_annual_falls_back_to_realized_month_when_ytd_zero():
    """In January when realized YTD is too short to extrapolate, the
    dashboard uses the more conservative `realized_month × 12` headline —
    and a `pending` month balance must not count as realized."""
    from app.api.cards import card_dashboard

    fixed_now = SimpleNamespace(month=1, year=2026, strftime=lambda fmt: "2026-01")

    db = _make_db_session(
        (0, Decimal("0")),
        Decimal("0"),
        # month: pending 500 (ignored), confirmed 100, paid_out 0 → realized 100
        (Decimal("500.00"), Decimal("100.00"), Decimal("0")),
        # ytd: realized 0 → fallback path
        (Decimal("500.00"), Decimal("0"), Decimal("0")),
    )

    with patch("app.api.cards.datetime") as mk_dt:
        mk_dt.now.return_value = fixed_now
        result = await card_dashboard(db=db, org=_org(), user=_user())

    assert result.projected_annual_rebates == pytest.approx(1200.0)


@pytest.mark.asyncio
async def test_projected_annual_is_zero_when_no_rebates_at_all():
    """A brand-new tenant with no rebates anywhere shouldn't crash
    or produce NaN — it should just report 0."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (0, Decimal("0")),
        Decimal("0"),
        (Decimal("0"), Decimal("0"), Decimal("0")),
        (Decimal("0"), Decimal("0"), Decimal("0")),
    )

    result = await card_dashboard(db=db, org=_org(), user=_user())

    assert result.projected_annual_rebates == 0.0
    assert result.rebates_this_month_by_status.pending_total == 0.0
    assert result.rebates_this_month_by_status.confirmed_total == 0.0
    assert result.rebates_this_month_by_status.paid_out_total == 0.0


# ---------------------------------------------------------------------------
# One currency per figure
#
# The rollups were bare cross-currency SUMs over `VirtualCard.amount_limit` /
# `.amount_charged` and `CardRebate.amount`, presented as a single headline
# with no currency code at all. A programme running USD and EUR cards had the
# two added together — not a quantity in either currency, and one that moved
# silently as the mix changed. `CardRebate` has no currency column of its own,
# so a rebate's currency is only knowable through the card that earned it.
#
# Recorded as an unverified lead in docs/followups.md ("Card and Positive Pay
# totals may mix currencies"); confirmed here.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_declares_the_currency_its_figures_are_in():
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (1, Decimal("100.00")),
        Decimal("0"),
        (Decimal("0"), Decimal("0"), Decimal("0")),
        (Decimal("0"), Decimal("0"), Decimal("0")),
    )
    result = await card_dashboard(db=db, org=_org(), user=_user())
    # No explicit setting → the platform default, via the canonical resolver.
    assert result.currency == "USD"


@pytest.mark.asyncio
async def test_dashboard_currency_follows_the_canonical_resolver():
    """Not just `settings.reporting_currency`: the whole resolution chain, so
    an org with only a home currency is labelled correctly."""
    from app.api.cards import card_dashboard

    for settings, expected in (
        ({"reporting_currency": "EUR"}, "EUR"),
        ({"payments": {"home_currency": "GBP"}}, "GBP"),
        ({"invoice_defaults": {"currency": "CAD"}}, "CAD"),
    ):
        db = _make_db_session(
            (0, Decimal("0")),
            Decimal("0"),
            (Decimal("0"), Decimal("0"), Decimal("0")),
            (Decimal("0"), Decimal("0"), Decimal("0")),
        )
        result = await card_dashboard(db=db, org=_org(settings), user=_user())
        assert result.currency == expected


@pytest.mark.asyncio
async def test_dashboard_reports_what_each_figure_left_out():
    """A partial figure must be visibly partial — counts only, because a
    cross-currency remainder has no single total to report."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (2, Decimal("500.00")),
        Decimal("120.00"),
        (Decimal("0"), Decimal("5.00"), Decimal("0")),
        (Decimal("0"), Decimal("20.00"), Decimal("0")),
        excluded_cards=4,
        excluded_rebates=3,
    )
    result = await card_dashboard(db=db, org=_org(), user=_user())
    assert result.excluded_card_count == 4
    assert result.excluded_rebate_count == 3
    # The in-currency figures are untouched by the exclusions.
    assert result.active_cards_value == 500.0
    assert result.rebates_ytd == 20.0


@pytest.mark.asyncio
async def test_single_currency_programme_reports_no_exclusions():
    """The common case must be unchanged."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (3, Decimal("10000.00")),
        Decimal("5000.00"),
        (Decimal("0"), Decimal("75.00"), Decimal("0")),
        (Decimal("0"), Decimal("400.00"), Decimal("0")),
    )
    result = await card_dashboard(db=db, org=_org(), user=_user())
    assert result.excluded_card_count == 0
    assert result.excluded_rebate_count == 0
    assert result.active_cards_value == 10000.0
    assert result.rebates_ytd == 400.0


@pytest.mark.asyncio
async def test_exclusion_counts_default_to_zero_when_the_count_is_null():
    """`func.count()` cannot return NULL, but the endpoint reads it with
    `.scalar() or 0` — pin the defensive branch so the response is always an
    int the frontend can render, never `None`."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (1, Decimal("10.00")),
        Decimal("0"),
        (Decimal("0"), Decimal("0"), Decimal("0")),
        (Decimal("0"), Decimal("0"), Decimal("0")),
        excluded_cards=None,
        excluded_rebates=None,
    )
    result = await card_dashboard(db=db, org=_org(), user=_user())
    assert result.excluded_card_count == 0
    assert result.excluded_rebate_count == 0


@pytest.mark.asyncio
async def test_money_fields_stay_exact_decimals():
    """The money invariant at the response boundary: `Decimal` in Python (the
    float hop happens once, at JSON-write time), so a sum of many small rebates
    is exact to the cent."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (3, Decimal("0.10")),
        Decimal("0.20"),
        (Decimal("0.10"), Decimal("0.10"), Decimal("0.10")),
        (Decimal("0.10"), Decimal("0.10"), Decimal("0.10")),
    )
    result = await card_dashboard(db=db, org=_org(), user=_user())

    for value in (
        result.active_cards_value,
        result.spend_this_month,
        result.rebates_this_month,
        result.rebates_ytd,
        result.projected_annual_rebates,
        result.rebates_this_month_by_status.pending_total,
    ):
        assert isinstance(value, Decimal), value
        assert not isinstance(value, float)
    # 0.10 + 0.10 realized, exactly — 0.2 in binary floats is 0.200000000000000011.
    assert result.rebates_ytd == Decimal("0.20")


@pytest.mark.asyncio
async def test_an_org_with_no_settings_at_all_still_declares_a_currency():
    """`Organization.settings` is nullable. A dashboard read must not 500 on a
    freshly provisioned tenant, and must not present money under no code."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (0, Decimal("0")),
        Decimal("0"),
        (Decimal("0"), Decimal("0"), Decimal("0")),
        (Decimal("0"), Decimal("0"), Decimal("0")),
    )
    result = await card_dashboard(
        db=db, org=SimpleNamespace(id="org-1", settings=None), user=_user()
    )
    assert result.currency == "USD"


@pytest.mark.asyncio
async def test_exclusion_counts_never_leak_into_a_money_figure():
    """A count is not a total: a cross-currency remainder has no single sum, so
    the counters must stay counts and must not be added to anything."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (1, Decimal("100.00")),
        Decimal("50.00"),
        (Decimal("0"), Decimal("5.00"), Decimal("0")),
        (Decimal("0"), Decimal("5.00"), Decimal("0")),
        excluded_cards=7,
        excluded_rebates=9,
    )
    result = await card_dashboard(db=db, org=_org(), user=_user())

    assert result.active_cards == 1
    assert result.active_cards_value == Decimal("100.00")
    assert result.spend_this_month == Decimal("50.00")
    assert result.rebates_ytd == Decimal("5.00")
    assert isinstance(result.excluded_card_count, int)
    assert isinstance(result.excluded_rebate_count, int)


def test_every_rebate_rollup_joins_the_card_that_carries_the_currency():
    """The structural guard behind the fix: `CardRebate` has NO currency column
    of its own, so a rebate's currency is only knowable through its card. Each
    of the three rebate queries (month, YTD, excluded-YTD) must therefore join
    `VirtualCard` — a refactor that drops a join is back to guessing, which is
    the defect."""
    import ast
    import inspect

    from app.api.cards import card_dashboard

    tree = ast.parse(inspect.getsource(card_dashboard).lstrip())
    statements = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.Expr | ast.Await)
    ]
    rebate_statements = [
        text
        for text in statements
        if "CardRebate." in text and ("func.sum" in text or "func.count" in text)
    ]
    assert rebate_statements, "no CardRebate aggregate found — did the rollups move?"
    for text in rebate_statements:
        assert "VirtualCard.id == CardRebate.virtual_card_id" in text, text
        assert "_card_ccy" in text, text


def test_every_card_rollup_filters_on_the_declared_currency():
    """The other half: each `VirtualCard` aggregate counts only rows in the
    declared currency, and the excluded counter is the exact complement (`!=`),
    so nothing is double-counted and nothing is silently dropped."""
    import inspect

    from app.api.cards import card_dashboard

    src = inspect.getsource(card_dashboard)
    assert src.count("_card_ccy == reporting_currency") == 4, src
    assert src.count("_card_ccy != reporting_currency") == 2, src
    # The card-currency expression itself now has ONE owner —
    # `currency_conversion.card_currency_sql`, shared by the six rollups that
    # were each spelling it. This asserts the delegation rather than the inline
    # literal it replaced; the coalesce + upper-case behaviour is asserted on
    # the helper in `test_rebate_currency_denomination.py`, which also fails any
    # module that re-derives it inline.
    assert "card_currency_sql(reporting_currency)" in src
    assert "func.coalesce(VirtualCard.currency" not in src, (
        "the currency expression is back inline; call card_currency_sql instead"
    )
