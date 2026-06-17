"""Early-payment optimizer — rank discount opportunities by ROI and pick which
to capture under a cash-budget constraint.

This is the "AI-optimized payment timing" brain of dynamic discounting: given a
set of open early-payment opportunities (each a single tier of a
:class:`~app.models.discount.DiscountOffer`, already reduced to "pay this much
by this date for this discount"), it answers *which* discounts to capture when
cash is finite.

Economic model — greedy by annualized return
---------------------------------------------
Every early-payment discount is a short-term *investment*: parting with cash
``days_accelerated`` early to capture a percentage off. The yield of that
investment is its annualized return (see
:func:`app.services.discount_roi.annualized_return` —
``discount% / (100 - discount%) * 365 / days``). The optimizer ranks every
worthwhile opportunity by that APR **descending** and selects greedily until
the cash budget is exhausted.

Why greedy-by-APR is the right rule here: APR already normalizes "return per
dollar per day of cash committed", so taking the highest-APR discount first
captures the most savings per unit of the scarce resource (cash). When the
budget binds, this maximizes total savings; when it doesn't (``cash_budget is
None``), every worthwhile opportunity is selected and ordering is purely
cosmetic. It is deliberately *not* a 0/1-knapsack solve — discounts are
near-divisible in practice (many small invoices), the inputs are advisory, and
a transparent "highest yield first" ranking is what a treasurer reasons about.

Purity
------
No DB, no network, no clock. ``today`` is passed in; all money is ``Decimal``
and the ROI math is delegated to :mod:`app.services.discount_roi` (never
duplicated here). The router maps the returned dataclasses onto the Pydantic
``OptimizerResponse``. See ``backend/docs/dynamic-discounting.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.services.discount_roi import DiscountROI, compute_roi, days_between

_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class OfferOpportunity:
    """One capturable early-payment opportunity (a single tier of an offer).

    ``base_amount`` — the amount the discount applies to.
    ``discount_percent`` — the tier's discount (e.g. ``Decimal("2.00")``).
    ``pay_by`` — the discount/tier deadline (latest pay date that still earns
    the tier).
    ``due_date`` — the invoice's net due date (the baseline you'd otherwise pay
    on). Days accelerated is ``days_between(pay_by, due_date)`` — the textbook
    cost-of-forgoing-discount horizon (pay on the deadline instead of at net),
    NOT the discount period itself. An opportunity whose ``pay_by`` has already
    elapsed (``today > pay_by``) is no longer capturable and is never selected.
    """

    offer_id: str
    invoice_id: str | None
    vendor_id: str | None
    vendor_name: str | None
    invoice_number: str | None
    base_amount: Decimal
    tier_days: int
    discount_percent: Decimal
    pay_by: date  # discount/tier deadline — latest pay date that still earns the tier
    due_date: date  # invoice net due date — the baseline you'd otherwise pay on


@dataclass(frozen=True)
class Recommendation:
    """One ranked opportunity + its ROI + whether it was selected."""

    opportunity: OfferOpportunity
    roi: DiscountROI
    selected: bool
    cumulative_outlay: Decimal  # running discounted cash committed through this rank


@dataclass
class OptimizationResult:
    """Outcome of one optimization pass."""

    cost_of_capital_pct: Decimal
    total_savings_available: Decimal  # sum of savings across *all* worthwhile opps
    total_savings_selected: Decimal  # sum of savings across selected opps
    total_outlay_selected: Decimal  # discounted cash committed to selected opps
    recommendations: list[Recommendation] = field(default_factory=list)


def optimize(
    opportunities: list[OfferOpportunity],
    *,
    cash_budget: Decimal | None,
    cost_of_capital_pct: Decimal,
    today: date,
) -> OptimizationResult:
    """Rank ``opportunities`` by annualized return and greedily select the
    highest-yield worthwhile ones that fit within ``cash_budget``.

    With ``cash_budget=None`` every worthwhile opportunity is selected. Only
    opportunities whose ROI is ``worthwhile`` (APR above the org's cost of
    capital) are ever eligible to be selected; non-worthwhile ones still appear
    in ``recommendations`` (ranked, ``selected=False``) for transparency.

    Returns a deterministic :class:`OptimizationResult`.
    """
    cost_of_capital_pct = Decimal(cost_of_capital_pct)

    # Score every opportunity with the shared ROI primitive.
    scored: list[tuple[OfferOpportunity, DiscountROI, bool]] = []
    for opp in opportunities:
        roi = compute_roi(
            base_amount=opp.base_amount,
            discount_percent=opp.discount_percent,
            days_accelerated=days_between(opp.pay_by, opp.due_date),
            cost_of_capital_pct=cost_of_capital_pct,
        )
        # Capturable only while the discount deadline has not elapsed.
        capturable = today <= opp.pay_by
        scored.append((opp, roi, capturable))

    # Rank by annualized return desc — highest yield per day of cash first.
    # Tie-break on larger savings, then offer_id for a stable deterministic order.
    scored.sort(
        key=lambda triple: (
            -triple[1].annualized_return_pct,
            -triple[1].savings,
            triple[0].offer_id,
        )
    )

    recommendations: list[Recommendation] = []
    total_savings_available = _ZERO
    total_savings_selected = _ZERO
    cumulative_outlay = _ZERO  # running discounted cash across *selected* opps only

    for opp, roi, capturable in scored:
        # Eligible = a worthwhile discount we can still capture (deadline open).
        eligible = roi.worthwhile and capturable
        if eligible:
            total_savings_available += roi.savings

        # Discounted cash outlay for this opportunity (what actually leaves the
        # bank if captured): base_amount - savings.
        outlay = roi.base_amount - roi.savings

        selected = False
        if eligible:
            if cash_budget is None:
                selected = True
            elif cumulative_outlay + outlay <= cash_budget:
                selected = True

        if selected:
            cumulative_outlay += outlay
            total_savings_selected += roi.savings

        recommendations.append(
            Recommendation(
                opportunity=opp,
                roi=roi,
                selected=selected,
                cumulative_outlay=cumulative_outlay,
            )
        )

    return OptimizationResult(
        cost_of_capital_pct=cost_of_capital_pct,
        total_savings_available=total_savings_available,
        total_savings_selected=total_savings_selected,
        total_outlay_selected=cumulative_outlay,
        recommendations=recommendations,
    )
