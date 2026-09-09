"""Unit tests for the early-payment ROI primitive (``services/discount_roi``).

Pure, no DB, no clock. This is the foundation the optimizer, the auto-capture
sweep and the per-invoice ROI endpoint all build on, so the arithmetic and — the
part this file exists for — the difference between a *measured* zero and an
*unknown* horizon are pinned here rather than at each consumer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.discount_roi import annualized_return, compute_roi, days_between

# --------------------------------------------------------------------------- #
# The textbook formula
# --------------------------------------------------------------------------- #


def test_two_ten_net_thirty_annualizes_to_about_37_percent():
    # 2 % for paying 20 days early: 2/98 * 365/20 * 100 ≈ 37.24 %.
    assert annualized_return(Decimal("2.00"), 20) == Decimal("37.24")


def test_no_acceleration_captures_no_time_value():
    assert annualized_return(Decimal("2.00"), 0) == Decimal("0.00")
    assert annualized_return(Decimal("2.00"), -5) == Decimal("0.00")


def test_degenerate_percentages_return_zero():
    assert annualized_return(Decimal("0"), 20) == Decimal("0.00")
    assert annualized_return(Decimal("100"), 20) == Decimal("0.00")


def test_days_between_floors_at_zero():
    assert days_between(date(2026, 1, 1), date(2026, 1, 21)) == 20
    assert days_between(date(2026, 1, 21), date(2026, 1, 1)) == 0


def test_compute_roi_is_decimal_exact_end_to_end():
    roi = compute_roi(
        base_amount=Decimal("10000.00"),
        discount_percent=Decimal("2.00"),
        days_accelerated=20,
        cost_of_capital_pct=Decimal("8.00"),
    )
    assert roi.savings == Decimal("200.00")
    # 9800 * 8% * 20/365 = 42.9589... -> 42.96 half-up.
    assert roi.opportunity_cost == Decimal("42.96")
    assert roi.net_benefit == Decimal("157.04")
    assert roi.annualized_return_pct == Decimal("37.24")
    assert roi.worthwhile is True
    assert roi.horizon_known is True
    for value in (roi.savings, roi.opportunity_cost, roi.net_benefit):
        assert isinstance(value, Decimal)


def test_a_discount_below_the_hurdle_rate_is_not_worthwhile():
    roi = compute_roi(
        base_amount=Decimal("10000.00"),
        discount_percent=Decimal("0.10"),
        days_accelerated=20,
        cost_of_capital_pct=Decimal("8.00"),
    )
    # 0.1/99.9 * 365/20 * 100 ≈ 1.83 % — below an 8 % cost of capital.
    assert roi.annualized_return_pct < roi.cost_of_capital_pct
    assert roi.worthwhile is False
    assert roi.horizon_known is True


# --------------------------------------------------------------------------- #
# An unknown horizon is UNKNOWN, not zero
# --------------------------------------------------------------------------- #


def test_compute_roi_withholds_every_horizon_relative_answer():
    """`days_accelerated=None` means the horizon is unknown, so the APR, the
    opportunity cost, the net benefit and the verdict are all `None` — never a
    fabricated 0.00 that reads as a measurement. `savings` survives: a
    percentage of a base amount needs no horizon."""
    roi = compute_roi(
        base_amount=Decimal("10000.00"),
        discount_percent=Decimal("5.00"),
        days_accelerated=None,
        cost_of_capital_pct=Decimal("8.00"),
    )
    assert roi.savings == Decimal("500.00")
    assert roi.days_accelerated is None
    assert roi.annualized_return_pct is None
    assert roi.opportunity_cost is None
    assert roi.net_benefit is None
    assert roi.worthwhile is None  # "cannot rank", NOT "no"
    assert roi.horizon_known is False


def test_unknown_horizon_audit_view_carries_nulls_not_placeholders():
    """`as_dict()` lands in an append-only audit row, so a placeholder there is
    a false record that cannot be corrected afterwards."""
    d = compute_roi(
        base_amount=Decimal("10000.00"),
        discount_percent=Decimal("5.00"),
        days_accelerated=None,
        cost_of_capital_pct=Decimal("8.00"),
    ).as_dict()
    assert d["days_accelerated"] is None
    assert d["annualized_return_pct"] is None
    assert d["opportunity_cost"] is None
    assert d["net_benefit"] is None
    assert d["worthwhile"] is None
    assert d["horizon_known"] is False
    assert d["savings"] == "500.00"  # exact-string money, still stated


def test_a_measured_zero_apr_is_distinguishable_from_an_unknown_one():
    """The whole point: a consumer must be able to tell "the return is 0 %"
    from "the return is unknown". Paying ON the due date really does capture no
    time value — that is a measurement, and it comes back as a number.

    Regression: the router used to substitute the discount deadline for a
    missing due date, collapsing the second case into the first. The object it
    produced contradicted itself — a 0.00 % APR and `worthwhile: false` beside a
    POSITIVE `net_benefit` — and every bulk-negotiated offer with no
    `valid_until` was permanently unrecommendable because of it.
    """
    measured = compute_roi(
        base_amount=Decimal("10000.00"),
        discount_percent=Decimal("5.00"),
        days_accelerated=0,
        cost_of_capital_pct=Decimal("8.00"),
    )
    assert measured.annualized_return_pct == Decimal("0.00")
    assert measured.worthwhile is False
    assert measured.horizon_known is True
    # This is the self-contradiction the collapse produced: a positive net
    # benefit under a zero return. It is CORRECT for a genuine zero-day
    # acceleration, which is exactly why it must not stand in for "unknown".
    assert measured.net_benefit == Decimal("500.00")

    unknown = compute_roi(
        base_amount=Decimal("10000.00"),
        discount_percent=Decimal("5.00"),
        days_accelerated=None,
        cost_of_capital_pct=Decimal("8.00"),
    )
    assert unknown.annualized_return_pct is None
    assert unknown.net_benefit is None
    assert unknown.horizon_known is False
