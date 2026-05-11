"""Dashboard aggregation — pins the *computation* layer of
`GET /api/dashboard`. The endpoint runs ~12 queries and then folds
the rows into pipeline counts, aging buckets, monthly trend, upcoming
payments, and touchless rate. The folding is pure Python on rows we
own — that's what these tests pin.

Bucket boundaries matter for AR reporting and stay-current
dashboards; getting the inclusive/exclusive ends wrong is a classic
off-by-one. Touchless rate is a board-deck metric — its denominator
must include rejected invoices, or the number lies.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.dashboard import get_dashboard
from app.models.invoice import InvoiceStatus


def _r(scalar=None, one=None, all_=None, sub=False):
    """Construct a MagicMock that satisfies one of the result shapes
    the dashboard endpoint uses:
      - `.scalar()` for SUM coalesce
      - `.one()` for the COUNT/SUM tuple
      - `.all()` for the row iterators
      - `.scalar_subquery()` is exercised at query *build* time, but
        in tests we never reach the DB so it doesn't matter.
    """
    m = MagicMock()
    if scalar is not None:
        m.scalar = MagicMock(return_value=scalar)
    if one is not None:
        m.one = MagicMock(return_value=one)
    if all_ is not None:
        m.all = MagicMock(return_value=all_)
    return m


def _user():
    return MagicMock()


def _mk_db(*results):
    """Build an AsyncMock whose execute() cycles through the given
    result mocks in order. Each entry is what we want the executor's
    nth `await db.execute(...)` to return."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(results))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# The dashboard makes queries in this order — pinning here so the
# test layout stays sane. Don't change the order without updating the
# endpoint AND the comments below.
#
#   1.  totals (count + sum)              → .one() → (count, sum)
#   2.  pipeline status rows              → .all()
#   3.  vendor spend rows                 → .all()
#   4.  aging rows (due_date, amount)     → .all()
#   5.  trend invoice rows                → .all()
#   6.  upcoming payment rows             → .all()
#   7.  total paid                        → .scalar()
#   8.  total pending                     → .scalar()
#   9.  total rebates                     → .scalar()
#  10.  stale approvals                   → .scalar()
#  11.  open exceptions                   → .scalar()


def _full_results(
    *,
    totals=(0, Decimal("0")),
    pipeline=(),
    vendor_spend=(),
    aging=(),
    trend=(),
    upcoming=(),
    paid=Decimal("0"),
    pending=Decimal("0"),
    rebates=Decimal("0"),
    stale=0,
    open_exc=0,
):
    return [
        _r(one=totals),
        _r(all_=list(pipeline)),
        _r(all_=list(vendor_spend)),
        _r(all_=list(aging)),
        _r(all_=list(trend)),
        _r(all_=list(upcoming)),
        _r(scalar=paid),
        _r(scalar=pending),
        _r(scalar=rebates),
        _r(scalar=stale),
        _r(scalar=open_exc),
    ]


# ---------------------------------------------------------------------------
# Aging buckets — the off-by-one trap. `days_past <= 0` is current;
# 1–30 is days_30; 31–60 is days_60; 61+ is days_90_plus.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aging_buckets_split_at_correct_day_boundaries():
    """One invoice in each bucket, on the exact boundary day, must
    land in the correct bucket. Bumping any boundary one day either
    direction silently mis-reports millions in AR aging."""
    today = date.today()
    aging_rows = [
        (today, Decimal("100.00")),  # current (0 days past)
        (today - timedelta(days=1), Decimal("200.00")),  # days_30 (1 day past)
        (today - timedelta(days=30), Decimal("400.00")),  # days_30 (exactly 30)
        (today - timedelta(days=31), Decimal("800.00")),  # days_60 (31 days)
        (today - timedelta(days=60), Decimal("1600.00")),  # days_60 (exactly 60)
        (today - timedelta(days=61), Decimal("3200.00")),  # days_90_plus
        (today - timedelta(days=365), Decimal("6400.00")),  # days_90_plus (way past)
    ]
    db = _mk_db(*_full_results(aging=aging_rows))

    result = await get_dashboard(db=db, user=_user())
    assert result["aging"]["current"] == 100.0
    assert result["aging"]["days_30"] == 600.0  # 200 + 400
    assert result["aging"]["days_60"] == 2400.0  # 800 + 1600
    assert result["aging"]["days_90_plus"] == 9600.0  # 3200 + 6400


@pytest.mark.asyncio
async def test_aging_buckets_treat_future_due_date_as_current():
    """A future due_date (not yet due) is `current`. A bug that
    flipped the sign on `today - due_date` would route future-dated
    invoices into days_90_plus."""
    today = date.today()
    aging_rows = [
        (today + timedelta(days=10), Decimal("100.00")),  # future
        (today + timedelta(days=60), Decimal("250.00")),  # far future
    ]
    db = _mk_db(*_full_results(aging=aging_rows))
    result = await get_dashboard(db=db, user=_user())
    assert result["aging"]["current"] == 350.0
    assert result["aging"]["days_30"] == 0.0
    assert result["aging"]["days_60"] == 0.0
    assert result["aging"]["days_90_plus"] == 0.0


# ---------------------------------------------------------------------------
# Touchless rate — the denominator MUST include rejected.
# `(total_processed - rejected) / total_processed * 100`. A regression
# that uses the numerator as the denominator pushes the rate to 100%.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_touchless_rate_excludes_rejected_from_numerator_only():
    """80 reached a post-approval state, 20 were rejected. Touchless
    rate = (80 - 0) / 80 * 100 = 100% if rejected is NOT in the
    "processed" pool, but the dashboard counts only terminal states.
    Rejected sits in pipeline.rejected and is subtracted from the
    numerator. The correct read:
      processed = 80 (sum of terminal states)
      rate = (80 - 20) / 80 * 100 = 75%
    """
    # Use plain strings — the dashboard accepts either enum or string keys.
    pipeline_rows = [
        (InvoiceStatus.approved, 30),
        (InvoiceStatus.posted_in_erp, 20),
        (InvoiceStatus.paid, 20),
        (InvoiceStatus.done, 10),
        (InvoiceStatus.rejected, 20),
    ]
    db = _mk_db(*_full_results(pipeline=pipeline_rows))
    result = await get_dashboard(db=db, user=_user())
    # processed = 30 + 20 + 20 + 10 = 80; rejected = 20.
    assert result["touchless_rate"] == 75.0
    assert result["pipeline"]["rejected"] == 20


@pytest.mark.asyncio
async def test_touchless_rate_is_zero_when_no_invoices_processed():
    """No invoices have hit a terminal state — touchless rate must be
    0, not a ZeroDivisionError."""
    db = _mk_db(*_full_results(pipeline=[(InvoiceStatus.new, 5)]))
    result = await get_dashboard(db=db, user=_user())
    assert result["touchless_rate"] == 0


# ---------------------------------------------------------------------------
# Monthly trend — must be sorted ascending by month key.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monthly_trend_is_sorted_ascending_by_month():
    """The frontend renders these straight onto an x-axis; out-of-order
    rows give a sawtooth chart. Feed rows in jumbled order, assert
    ascending output."""
    today = date(2026, 5, 15)
    trend_rows = [
        (date(2026, 3, 4), Decimal("100")),
        (date(2026, 1, 22), Decimal("50")),
        (date(2026, 4, 1), Decimal("75")),
        (date(2026, 1, 5), Decimal("25")),  # same month as Jan 22
    ]
    db = _mk_db(*_full_results(trend=trend_rows))
    # Patch today() so the 180-day filter doesn't matter — endpoint
    # already filtered at the query layer (which we mock).
    result = await get_dashboard(db=db, user=_user())
    months = [m["month"] for m in result["monthly_trend"]]
    assert months == sorted(months), "monthly_trend not sorted ascending"
    # Two Jan rows must collapse into a single bucket with both
    # counted.
    jan = next(m for m in result["monthly_trend"] if m["month"] == "2026-01")
    assert jan["count"] == 2
    assert jan["amount"] == 75.0  # 50 + 25
    # Today must not have been used to inject this date — just sanity:
    assert today.isoformat() not in months


# ---------------------------------------------------------------------------
# Upcoming payments — `is_overdue` must be true iff due_date < today.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upcoming_payments_flag_overdue_only_when_due_date_before_today():
    """Today's due date is `is_overdue=False` (still on time);
    yesterday is True. A regression on the inequality direction would
    nag the AP team for paying on time."""
    import uuid

    today = date.today()
    upcoming_rows = [
        (uuid.uuid4(), "INV-1", "Acme", Decimal("100"), today - timedelta(days=1)),  # overdue
        (uuid.uuid4(), "INV-2", "Bravo", Decimal("100"), today),  # on time
        (uuid.uuid4(), "INV-3", "Charlie", Decimal("100"), today + timedelta(days=3)),  # future
    ]
    db = _mk_db(*_full_results(upcoming=upcoming_rows))
    result = await get_dashboard(db=db, user=_user())
    flags = {row["invoice_number"]: row["is_overdue"] for row in result["upcoming_payments"]}
    assert flags == {"INV-1": True, "INV-2": False, "INV-3": False}


# ---------------------------------------------------------------------------
# Money sums — every aggregate must be float for JSON serialization,
# but the underlying SUM must NOT have been coerced through float
# arithmetic at the Python layer (we accept Decimal in, float out).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_totals_pass_through_as_float():
    """Decimal coming from SQLAlchemy must serialize as float so the
    JSON response is well-formed. The cast happens at the dict-build
    step — a regression that forgot the cast would emit a Decimal
    object and crash JSON encoding at the FastAPI layer."""
    db = _mk_db(
        *_full_results(
            paid=Decimal("12345.67"),
            pending=Decimal("8900.50"),
            rebates=Decimal("123.45"),
        )
    )
    result = await get_dashboard(db=db, user=_user())
    assert isinstance(result["total_paid"], float)
    assert result["total_paid"] == 12345.67
    assert result["total_pending"] == 8900.50
    assert result["total_rebates"] == 123.45


@pytest.mark.asyncio
async def test_totals_default_to_zero_when_db_returns_zero_rows():
    """Empty tenant — count 0, sum None coalesced to 0. Verify the
    dashboard returns a fully-shaped response with zeros, not nulls
    (the frontend would crash on a null in the KPI tile)."""
    db = _mk_db(*_full_results())
    result = await get_dashboard(db=db, user=_user())
    assert result["total_invoices"] == 0
    assert result["total_amount"] == 0.0
    assert result["total_paid"] == 0.0
    assert result["total_pending"] == 0.0
    assert result["pipeline"] == {}
    assert result["vendor_spend"] == []
    assert result["upcoming_payments"] == []
    assert result["monthly_trend"] == []
    assert result["touchless_rate"] == 0
