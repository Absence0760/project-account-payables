"""The subscription billing period — pure resolution rules.

``services/billing/period.py`` exists because nothing wrote
``Subscription.current_period_start`` / ``current_period_end``, so every
mid-period plan change divided by a zero-length window and prorated
``0.00``. These pin the calendar arithmetic and the resolution precedence; the
end-to-end proof that a real subscription now prorates a real figure lives in
``test_billing_proration.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.billing.period import add_months, current_period, resolve_period


def test_add_months_clamps_the_day_of_month():
    # 31 Jan + 1 month is the end of February, never 3 March.
    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    # ...and lands on the 29th in a leap year.
    assert add_months(datetime(2028, 1, 31, tzinfo=UTC), 1) == datetime(2028, 2, 29, tzinfo=UTC)


def test_add_months_crosses_the_year_boundary_and_preserves_time():
    assert add_months(datetime(2026, 11, 15, 9, 30, tzinfo=UTC), 3) == datetime(
        2027, 2, 15, 9, 30, tzinfo=UTC
    )


def test_add_months_walks_backwards():
    assert add_months(datetime(2026, 3, 31, tzinfo=UTC), -1) == datetime(2026, 2, 28, tzinfo=UTC)


def test_resolve_period_returns_the_window_containing_now():
    anchor = datetime(2026, 1, 10, tzinfo=UTC)
    period = resolve_period(anchor=anchor, now=datetime(2026, 3, 25, tzinfo=UTC))
    assert period.start == datetime(2026, 3, 10, tzinfo=UTC)
    assert period.end == datetime(2026, 4, 10, tzinfo=UTC)
    assert not period.is_degenerate


def test_resolve_period_boundary_belongs_to_the_period_it_opens():
    """Half-open: ``start <= t < end``, so the boundary instant is the NEW
    period's first moment, never the old one's last."""
    anchor = datetime(2026, 1, 10, tzinfo=UTC)
    period = resolve_period(anchor=anchor, now=datetime(2026, 2, 10, tzinfo=UTC))
    assert period.start == datetime(2026, 2, 10, tzinfo=UTC)
    assert period.end == datetime(2026, 3, 10, tzinfo=UTC)


def test_resolve_period_is_never_degenerate_when_now_precedes_the_anchor():
    """Clock skew (or a subscription dated in the future) must still yield a
    real denominator — a zero-length window is exactly what made
    ``compute_proration`` short-circuit to 0.00."""
    anchor = datetime(2026, 5, 1, tzinfo=UTC)
    period = resolve_period(anchor=anchor, now=datetime(2026, 4, 1, tzinfo=UTC))
    assert period.start == anchor
    assert period.end == datetime(2026, 6, 1, tzinfo=UTC)
    assert not period.is_degenerate


def test_resolve_period_survives_a_month_end_anchor():
    """A 31st anchor clamps in short months but must not drift off it."""
    anchor = datetime(2026, 1, 31, tzinfo=UTC)
    period = resolve_period(anchor=anchor, now=datetime(2026, 3, 15, tzinfo=UTC))
    assert period.start == datetime(2026, 2, 28, tzinfo=UTC)
    assert period.end == datetime(2026, 3, 31, tzinfo=UTC)


def test_current_period_honours_a_persisted_window_that_contains_now():
    """A provider-synced window (once that lands) must not be recomputed."""
    sub = SimpleNamespace(
        current_period_start=datetime(2026, 6, 3, tzinfo=UTC),
        current_period_end=datetime(2026, 7, 5, tzinfo=UTC),  # not a whole month
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    period = current_period(sub, now=datetime(2026, 6, 20, tzinfo=UTC))
    assert period.start == datetime(2026, 6, 3, tzinfo=UTC)
    assert period.end == datetime(2026, 7, 5, tzinfo=UTC)


def test_current_period_rolls_a_stale_persisted_window_forward():
    sub = SimpleNamespace(
        current_period_start=datetime(2026, 1, 5, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 5, tzinfo=UTC),
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    period = current_period(sub, now=datetime(2026, 6, 20, tzinfo=UTC))
    assert period.start == datetime(2026, 6, 5, tzinfo=UTC)
    assert period.end == datetime(2026, 7, 5, tzinfo=UTC)


def test_current_period_falls_back_to_created_at_when_bounds_are_null():
    """The legacy row: every subscription created before the window was
    stamped carries NULL bounds, and `created_at` is the only anchor there is."""
    sub = SimpleNamespace(
        current_period_start=None,
        current_period_end=None,
        created_at=datetime(2026, 2, 14, tzinfo=UTC),
    )
    period = current_period(sub, now=datetime(2026, 5, 1, tzinfo=UTC))
    assert period.start == datetime(2026, 4, 14, tzinfo=UTC)
    assert period.end == datetime(2026, 5, 14, tzinfo=UTC)


def test_current_period_never_returns_a_degenerate_window_for_a_bare_row():
    """Belt and braces: nothing on the row at all still yields a real month,
    because a zero-length window is what silently zeroed every proration."""
    now = datetime(2026, 8, 18, tzinfo=UTC)
    period = current_period(SimpleNamespace(), now=now)
    assert period.start == now
    assert period.end == add_months(now, 1)
    assert period.end - period.start > timedelta(days=27)
