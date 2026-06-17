"""Early-payment ROI calculator — the shared primitive for dynamic discounting.

Pure, deterministic, no DB / network. Given a discount (percent + the number
of days payment is accelerated), it answers two questions:

  1. What is the *annualized return* of taking the discount? Paying early to
     capture a 2% discount 20 days before the due date is economically a
     short-term investment; the standard cost-of-forgoing-discount formula
     annualizes it::

         APR = discount% / (100 - discount%) * 365 / days_accelerated

     A 2/10-net-30 discount (2% for paying 20 days early) annualizes to
     ~37.2% — far above any normal cost of capital, which is why capturing
     early-pay discounts is almost always worthwhile.

  2. Is it *worthwhile* for this org? The annualized return is compared to the
     org's cost of capital (the opportunity cost of the cash). `net_benefit`
     is the dollar discount minus the opportunity cost of parting with the
     cash `days_accelerated` early.

Everything is ``Decimal`` — currency is never float (project invariant). The
optimizer (``discount_optimizer``) and the auto-capture sweep
(``discount_auto_trigger``) both build on this module. See
``backend/docs/dynamic-discounting.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_CENTS = Decimal("0.01")
_PCT = Decimal("0.01")
_DAYS_PER_YEAR = Decimal("365")
_HUNDRED = Decimal("100")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class DiscountROI:
    """Outcome of evaluating one early-payment discount opportunity."""

    base_amount: Decimal
    discount_percent: Decimal
    days_accelerated: int
    savings: Decimal  # dollar discount captured
    annualized_return_pct: Decimal  # APR of taking the discount
    cost_of_capital_pct: Decimal  # the hurdle rate compared against
    opportunity_cost: Decimal  # cost of parting with cash `days_accelerated` early
    net_benefit: Decimal  # savings - opportunity_cost
    worthwhile: bool  # annualized_return_pct > cost_of_capital_pct

    def as_dict(self) -> dict:
        """JSON-/audit-friendly view — money + percents as Decimal-strings."""
        return {
            "base_amount": str(self.base_amount),
            "discount_percent": str(self.discount_percent),
            "days_accelerated": self.days_accelerated,
            "savings": str(self.savings),
            "annualized_return_pct": str(self.annualized_return_pct),
            "cost_of_capital_pct": str(self.cost_of_capital_pct),
            "opportunity_cost": str(self.opportunity_cost),
            "net_benefit": str(self.net_benefit),
            "worthwhile": self.worthwhile,
        }


def annualized_return(discount_percent: Decimal, days_accelerated: int) -> Decimal:
    """Annualized return (APR %) of capturing ``discount_percent`` by paying
    ``days_accelerated`` days early.

    Returns ``0`` when there is no acceleration (paying on/after the due date
    captures nothing of time value) or a degenerate/over-100% discount.
    """
    if days_accelerated <= 0:
        return Decimal("0.00")
    if discount_percent <= 0 or discount_percent >= _HUNDRED:
        return Decimal("0.00")
    apr = (
        discount_percent
        / (_HUNDRED - discount_percent)
        * (_DAYS_PER_YEAR / Decimal(days_accelerated))
        * _HUNDRED
    )
    return _q_pct(apr)


def compute_roi(
    *,
    base_amount: Decimal,
    discount_percent: Decimal,
    days_accelerated: int,
    cost_of_capital_pct: Decimal,
) -> DiscountROI:
    """Evaluate one early-payment discount opportunity.

    ``base_amount`` — the amount the discount applies to (invoice amount, or a
    vendor's summed open balance for a bulk offer).
    ``days_accelerated`` — how many days before the due date the payment lands
    (``(due_date - pay_date).days``; clamp negatives to 0 at the call site if
    you prefer, this function treats <= 0 as "no time value captured").
    ``cost_of_capital_pct`` — the org's annual cost of capital (hurdle rate).
    """
    base_amount = Decimal(base_amount)
    discount_percent = Decimal(discount_percent)
    cost_of_capital_pct = Decimal(cost_of_capital_pct)
    days = max(0, int(days_accelerated))

    savings = _q_money(base_amount * discount_percent / _HUNDRED)
    apr = annualized_return(discount_percent, days)
    # Opportunity cost of paying the (already-discounted) cash `days` early.
    paid_now = base_amount - savings
    opportunity_cost = _q_money(
        paid_now * cost_of_capital_pct / _HUNDRED * Decimal(days) / _DAYS_PER_YEAR
    )
    net_benefit = _q_money(savings - opportunity_cost)
    return DiscountROI(
        base_amount=_q_money(base_amount),
        discount_percent=_q_pct(discount_percent),
        days_accelerated=days,
        savings=savings,
        annualized_return_pct=apr,
        cost_of_capital_pct=_q_pct(cost_of_capital_pct),
        opportunity_cost=opportunity_cost,
        net_benefit=net_benefit,
        worthwhile=apr > cost_of_capital_pct,
    )


def days_between(pay_date: date, due_date: date) -> int:
    """Days payment is accelerated — ``(due_date - pay_date).days``, floored at 0."""
    return max(0, (due_date - pay_date).days)
