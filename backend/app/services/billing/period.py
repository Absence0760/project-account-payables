"""The subscription's current billing period — one rule, three readers.

``Subscription.current_period_start`` / ``current_period_end`` are declared on
the model and read in three places that all depend on them meaning something:

* ``plan_change.change_plan`` — the window ``compute_proration`` divides by;
* ``dunning_sweep.run_dunning_once`` — the clock the grace window runs off;
* ``GET /api/billing/subscription`` — what the customer is shown.

Nothing wrote them. ``plan_catalog.ensure_subscription`` is the only place a
``Subscription`` row is constructed outside tests and it set
``id``/``organization_id``/``plan_id``/``status`` and nothing else, so both
columns were permanently ``NULL``. The consequences were not cosmetic:

* ``change_plan`` passed ``current_period_start or now`` and
  ``current_period_end or now`` into ``compute_proration``, whose
  degenerate-window guard then short-circuited to ``Decimal("0.00")``. **Every**
  mid-period plan change prorated nothing, returned that to the ``/billing``
  UI under an "applies immediately, prorates the current period" notice, and
  wrote ``proration_amount: "0.00"`` into the immutable ``billing.plan_changed``
  audit row. An upgrade from $49 to $499 on day 2 of the month adjusted nothing.
* The dunning sweep's "no period end recorded ⇒ overdue by default" edge case
  was the *only* case, so ``FEOH_BILLING_DUNNING_GRACE_DAYS`` could never apply
  to any subscription that can exist.

Plans are flat **monthly** (``Plan.monthly_price``), so the period is a
calendar month anchored on when the subscription started. That is derived here
rather than guessed at each call site, and it is deliberately a *pure* function
of ``(anchor, now)`` so the three readers cannot disagree about which period a
subscription is in.

**The provider is authoritative once it is wired.** A live
``stripe_billing`` subscription has its own period boundaries, and when
``ProviderSubscription`` starts carrying them the synced values must win — this
module is what a tenant has in the meantime, and what the ``mock`` provider
(which has no opinion about periods) has permanently. Persisting the resolved
window is therefore always safe to overwrite from a provider sync later.

Pure: no DB, no clock of its own, no I/O.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime

#: How far a subscription can lag before ``resolve_period`` stops walking month
#: by month. A row whose anchor is decades stale still resolves in one step via
#: the arithmetic below, but the loop is bounded so a pathological anchor (a
#: clock skew putting ``now`` before the anchor, a corrupted timestamp) can
#: never spin.
_MAX_PERIODS_FORWARD = 1200  # 100 years of monthly periods


def add_months(when: datetime, months: int) -> datetime:
    """``when`` shifted by whole calendar months, clamping the day.

    31 Jan + 1 month is 28/29 Feb, not 3 March — the convention every monthly
    subscription uses. Timezone and time-of-day are preserved.
    """
    total = when.month - 1 + months
    year = when.year + total // 12
    month = total % 12 + 1
    day = min(when.day, calendar.monthrange(year, month)[1])
    return when.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class BillingPeriod:
    """One monthly billing window, half-open: ``start <= t < end``."""

    start: datetime
    end: datetime

    @property
    def is_degenerate(self) -> bool:
        return self.end <= self.start


def resolve_period(*, anchor: datetime, now: datetime) -> BillingPeriod:
    """The monthly window containing ``now``, counting from ``anchor``.

    ``anchor`` is when the subscription started billing (its persisted
    ``current_period_start``, else its ``created_at``). Periods run
    ``anchor + k months`` → ``anchor + (k+1) months``; the one containing
    ``now`` is returned. ``now`` before ``anchor`` (clock skew, a subscription
    dated in the future) yields the first period, so the result is never
    degenerate and ``compute_proration`` always has a real denominator.
    """
    if now <= anchor:
        return BillingPeriod(start=anchor, end=add_months(anchor, 1))

    # Jump straight to the right month instead of stepping: the month delta is
    # an upper bound on k, and at most one step back corrects for a day-of-month
    # that hasn't been reached yet.
    k = (now.year - anchor.year) * 12 + (now.month - anchor.month)
    k = max(0, min(k, _MAX_PERIODS_FORWARD))
    while k > 0 and add_months(anchor, k) > now:
        k -= 1
    start = add_months(anchor, k)
    end = add_months(anchor, k + 1)
    while end <= now and k < _MAX_PERIODS_FORWARD:
        k += 1
        start, end = end, add_months(anchor, k + 1)
    return BillingPeriod(start=start, end=end)


def current_period(subscription, *, now: datetime) -> BillingPeriod:
    """The billing period ``subscription`` is in as of ``now``.

    Uses the persisted ``current_period_start`` as the anchor when there is
    one — that is the subscription's real billing day-of-month — and falls back
    to ``created_at`` for a row that predates this module. A persisted window
    that already contains ``now`` is returned verbatim, so a provider-synced
    window (once that lands) is never silently recomputed.
    """
    start = getattr(subscription, "current_period_start", None)
    end = getattr(subscription, "current_period_end", None)
    if start is not None and end is not None and start <= now < end:
        return BillingPeriod(start=start, end=end)
    anchor = start or getattr(subscription, "created_at", None) or now
    return resolve_period(anchor=anchor, now=now)
