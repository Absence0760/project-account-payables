"""Unit tests for the early-payment optimizer (``services/discount_optimizer``).

Pure logic — no DB, no clock, no network. Covers the ROI-ranking rule, the
greedy cash-budget selection, the worthwhile gate, Decimal exactness, and the
rollup totals.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.services.discount_optimizer import (
    OfferOpportunity,
    optimize,
)

_TODAY = date(2026, 1, 1)


def _opp(
    offer_id: str,
    *,
    base: str,
    percent: str,
    pay_by: date,
    due_date: date | None = None,
    tier_days: int = 10,
) -> OfferOpportunity:
    # Default the net due date 20 days after the discount deadline, so
    # days_accelerated = days_between(pay_by, due_date) = 20 unless overridden.
    return OfferOpportunity(
        offer_id=offer_id,
        invoice_id=f"inv-{offer_id}",
        vendor_id=f"ven-{offer_id}",
        vendor_name=f"Vendor {offer_id}",
        invoice_number=f"INV-{offer_id}",
        base_amount=Decimal(base),
        tier_days=tier_days,
        discount_percent=Decimal(percent),
        pay_by=pay_by,
        due_date=due_date if due_date is not None else pay_by + timedelta(days=20),
    )


def test_empty_input_returns_empty_result():
    result = optimize(
        [], cash_budget=None, cost_of_capital_pct=Decimal("8.00"), today=_TODAY
    )
    assert result.recommendations == []
    assert result.total_savings_available == Decimal("0.00")
    assert result.total_savings_selected == Decimal("0.00")
    assert result.total_outlay_selected == Decimal("0.00")
    assert result.cost_of_capital_pct == Decimal("8.00")


def test_no_budget_selects_every_worthwhile_opportunity():
    opps = [
        # 2% off, paid 20 days early (deadline→due) → APR ~37% (worthwhile vs 8%)
        _opp("a", base="1000.00", percent="2.00", pay_by=date(2026, 1, 21)),
        # 1% off, paid 20 days early → APR ~18% (worthwhile)
        _opp("b", base="5000.00", percent="1.00", pay_by=date(2026, 1, 21)),
    ]
    result = optimize(
        opps, cash_budget=None, cost_of_capital_pct=Decimal("8.00"), today=_TODAY
    )
    assert all(r.selected for r in result.recommendations)
    # savings = 1000*2% + 5000*1% = 20 + 50
    assert result.total_savings_selected == Decimal("70.00")
    assert result.total_savings_available == Decimal("70.00")


def test_ranking_is_annualized_return_descending():
    # Both worthwhile, but 'a' (2% over 20d) has a far higher APR than the
    # bigger-dollar 'b' (1% over 20d). Highest APR must rank first.
    opps = [
        _opp("b", base="5000.00", percent="1.00", pay_by=date(2026, 1, 21)),
        _opp("a", base="1000.00", percent="2.00", pay_by=date(2026, 1, 21)),
    ]
    result = optimize(
        opps, cash_budget=None, cost_of_capital_pct=Decimal("8.00"), today=_TODAY
    )
    ranked_ids = [r.opportunity.offer_id for r in result.recommendations]
    assert ranked_ids == ["a", "b"]
    assert (
        result.recommendations[0].roi.annualized_return_pct
        > result.recommendations[1].roi.annualized_return_pct
    )


def test_non_worthwhile_opportunities_are_never_selected():
    # A tiny 0.1% discount over the default 20-day horizon → APR ~1.8%, well
    # under an 8% cost of capital.
    opp = _opp("low", base="1000.00", percent="0.10", pay_by=date(2026, 2, 1))
    result = optimize(
        [opp], cash_budget=None, cost_of_capital_pct=Decimal("8.00"), today=_TODAY
    )
    rec = result.recommendations[0]
    assert rec.roi.worthwhile is False
    assert rec.selected is False
    assert result.total_savings_available == Decimal("0.00")
    assert result.total_savings_selected == Decimal("0.00")


def test_greedy_stops_when_budget_exhausted():
    # Two worthwhile opps. Budget only covers the discounted outlay of the
    # first-ranked (highest-APR) one.
    high = _opp("high", base="1000.00", percent="2.00", pay_by=date(2026, 1, 21))
    low = _opp("low", base="5000.00", percent="1.00", pay_by=date(2026, 1, 21))
    # 'high' outlay = 1000 - 20 = 980. Budget 1000 fits 'high' but not also 'low'.
    result = optimize(
        [high, low],
        cash_budget=Decimal("1000.00"),
        cost_of_capital_pct=Decimal("8.00"),
        today=_TODAY,
    )
    by_id = {r.opportunity.offer_id: r for r in result.recommendations}
    assert by_id["high"].selected is True
    assert by_id["low"].selected is False
    assert result.total_outlay_selected == Decimal("980.00")
    assert result.total_savings_selected == Decimal("20.00")
    # availability counts *all* worthwhile, selected or not.
    assert result.total_savings_available == Decimal("70.00")


def test_budget_skips_unaffordable_then_takes_a_later_affordable_one():
    # Highest-APR opp is too big for the budget; a lower-APR but cheaper opp
    # should still be picked up greedily after it.
    big = _opp("big", base="10000.00", percent="2.00", pay_by=date(2026, 1, 21))
    small = _opp("small", base="500.00", percent="1.50", pay_by=date(2026, 1, 21))
    # big outlay = 10000 - 200 = 9800 (too big for 1000 budget).
    # small outlay = 500 - 7.50 = 492.50 (fits).
    result = optimize(
        [big, small],
        cash_budget=Decimal("1000.00"),
        cost_of_capital_pct=Decimal("8.00"),
        today=_TODAY,
    )
    by_id = {r.opportunity.offer_id: r for r in result.recommendations}
    assert by_id["big"].selected is False
    assert by_id["small"].selected is True
    assert result.total_outlay_selected == Decimal("492.50")
    assert result.total_savings_selected == Decimal("7.50")


def test_cumulative_outlay_tracks_only_selected():
    a = _opp("a", base="1000.00", percent="2.00", pay_by=date(2026, 1, 21))
    b = _opp("b", base="2000.00", percent="2.00", pay_by=date(2026, 1, 21))
    result = optimize(
        [a, b], cash_budget=None, cost_of_capital_pct=Decimal("8.00"), today=_TODAY
    )
    # ranked by APR desc; equal APR → larger savings first → 'b' (40) before 'a' (20).
    recs = result.recommendations
    assert recs[0].opportunity.offer_id == "b"
    assert recs[0].cumulative_outlay == Decimal("1960.00")  # 2000 - 40
    assert recs[1].cumulative_outlay == Decimal("2940.00")  # + (1000 - 20)
    assert result.total_outlay_selected == Decimal("2940.00")


def test_money_is_exact_decimal_no_float():
    opp = _opp("x", base="333.33", percent="3.00", pay_by=date(2026, 1, 11))
    result = optimize(
        [opp], cash_budget=None, cost_of_capital_pct=Decimal("8.00"), today=_TODAY
    )
    rec = result.recommendations[0]
    # 333.33 * 3% = 9.9999 → quantized to 10.00 by the ROI primitive.
    assert rec.roi.savings == Decimal("10.00")
    assert isinstance(rec.roi.savings, Decimal)
    assert isinstance(result.total_savings_selected, Decimal)
    assert rec.cumulative_outlay == Decimal("323.33")  # 333.33 - 10.00


def test_past_deadline_is_not_capturable_and_never_selected():
    # The discount deadline (pay_by) elapsed before `today` — even though the
    # discount itself would be worthwhile, we can no longer capture it, so it is
    # never selected and contributes nothing to available savings.
    opp = _opp("late", base="1000.00", percent="2.00", pay_by=date(2025, 12, 25))
    result = optimize(
        [opp], cash_budget=None, cost_of_capital_pct=Decimal("8.00"), today=_TODAY
    )
    rec = result.recommendations[0]
    assert rec.roi.worthwhile is True  # the discount is economically good...
    assert rec.selected is False  # ...but the deadline has passed.
    assert result.total_savings_available == Decimal("0.00")
    assert result.total_savings_selected == Decimal("0.00")
