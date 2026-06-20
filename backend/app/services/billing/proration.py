"""Mid-period plan-change proration — pure, Decimal-exact.

When a subscription changes plan part-way through a billing period, the customer
has already paid (or will be billed) for the OLD plan's full period. The fair
adjustment is:

    proration = (new_monthly - old_monthly) * (unused_days / period_days)

i.e. credit the customer for the unused portion of the old plan and charge for
the same unused portion of the new plan. A **positive** result is an extra
charge (upgrade — the new plan costs more), a **negative** result is a credit
(downgrade), and a same-price (or same-plan) change is exactly ``Decimal("0.00")``.

Money invariant
---------------
Every amount is an exact :class:`~decimal.Decimal`; there is no float anywhere in
this module. The two prices and the day counts are combined with Decimal
arithmetic and the result is quantized to 2 decimal places.

Rounding rule
-------------
The final proration amount is quantized to **2 decimal places** using
``ROUND_HALF_UP`` (round half away from zero — the convention finance teams and
invoices expect; e.g. ``0.005 -> 0.01``). Intermediate products are kept at full
Decimal precision and rounded exactly once, at the end, so no rounding error
accumulates across the multiply/divide.

This is a *pure* function: it never reads the DB, never calls a provider, and
never moves money. The plan-change service feeds the result through the billing
adapter (which is what actually bills, when a live provider is wired); locally
the mock provider no-ops, so the proration is an informational figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

# Quantum for a 2-dp money amount.
_CENTS = Decimal("0.01")


@dataclass(frozen=True)
class ProrationResult:
    """The computed mid-period proration for a plan change.

    ``amount`` is the net adjustment in the plan currency: positive = extra
    charge (upgrade), negative = credit (downgrade), ``Decimal("0.00")`` = no
    change. ``unused_days`` / ``period_days`` are surfaced for the audit row and
    tests so the math is transparent.
    """

    amount: Decimal
    unused_days: int
    period_days: int
    old_monthly: Decimal
    new_monthly: Decimal


def compute_proration(
    *,
    old_monthly: Decimal,
    new_monthly: Decimal,
    period_start: datetime,
    period_end: datetime,
    change_at: datetime,
) -> ProrationResult:
    """Prorate a mid-period plan change. Pure + Decimal-exact.

    Args:
        old_monthly: the current plan's flat monthly price (exact Decimal).
        new_monthly: the target plan's flat monthly price (exact Decimal).
        period_start / period_end: the subscription's current billing window.
        change_at: the instant the change takes effect (clamped into the window).

    The unused portion is whole days remaining from ``change_at`` to
    ``period_end`` (``ceil`` is avoided — we count remaining *full* days, floor,
    consistent with day-granular SaaS proration). ``change_at`` outside the
    window clamps to the nearest boundary (a change before the period starts
    prorates the whole period; after it ends prorates nothing).

    Returns a :class:`ProrationResult`. A same-price change (or zero/negative
    period) yields ``Decimal("0.00")`` without any division.
    """
    # Defensive: a degenerate or inverted window has no meaningful proration.
    total_seconds = (period_end - period_start).total_seconds()
    if total_seconds <= 0:
        return ProrationResult(
            amount=Decimal("0.00"),
            unused_days=0,
            period_days=0,
            old_monthly=old_monthly,
            new_monthly=new_monthly,
        )

    period_days = _whole_days(period_start, period_end)

    # Clamp the change instant into [period_start, period_end].
    effective = change_at
    if effective < period_start:
        effective = period_start
    elif effective > period_end:
        effective = period_end

    unused_days = _whole_days(effective, period_end)
    # Never count more unused than the period itself (guards a clamp edge).
    if unused_days > period_days:
        unused_days = period_days

    price_delta = new_monthly - old_monthly
    if price_delta == 0 or period_days == 0 or unused_days == 0:
        # Same price, no remaining days, or zero-length period → no adjustment.
        return ProrationResult(
            amount=Decimal("0.00"),
            unused_days=unused_days,
            period_days=period_days,
            old_monthly=old_monthly,
            new_monthly=new_monthly,
        )

    # Exact Decimal math: keep full precision through the multiply/divide, round
    # exactly once at the end (ROUND_HALF_UP, 2 dp).
    raw = price_delta * Decimal(unused_days) / Decimal(period_days)
    amount = raw.quantize(_CENTS, rounding=ROUND_HALF_UP)

    return ProrationResult(
        amount=amount,
        unused_days=unused_days,
        period_days=period_days,
        old_monthly=old_monthly,
        new_monthly=new_monthly,
    )


def _whole_days(start: datetime, end: datetime) -> int:
    """Whole days from ``start`` to ``end`` (floored, never negative)."""
    delta_seconds = (end - start).total_seconds()
    if delta_seconds <= 0:
        return 0
    return int(delta_seconds // 86400)
