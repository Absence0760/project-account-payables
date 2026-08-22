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


def _make_db_session(active_row, spend_scalar, rebate_month_row, rebate_ytd_row):
    """Build a session that walks the endpoint's four queries in order:

    1. `active_q`          → `.one()`  → (active_count, active_value)
    2. `spend_q`           → `.scalar()` → spend_this_month
    3. `rebate_month_q`    → `.one()`  → (pending, confirmed, paid_out)
    4. `rebate_ytd_q`      → `.one()`  → (pending, confirmed, paid_out)
    """
    session = AsyncMock()

    results = []

    active_result = MagicMock()
    active_result.one.return_value = active_row
    results.append(active_result)

    spend_result = MagicMock()
    spend_result.scalar.return_value = spend_scalar
    results.append(spend_result)

    month_result = MagicMock()
    month_result.one.return_value = rebate_month_row
    results.append(month_result)

    ytd_result = MagicMock()
    ytd_result.one.return_value = rebate_ytd_row
    results.append(ytd_result)

    async def execute_side_effect(_query):
        return results.pop(0)

    session.execute.side_effect = execute_side_effect
    return session


def _user():
    return SimpleNamespace(id="user-1", roles=["admin"])


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

    result = await card_dashboard(db=db, user=_user())

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

    result = await card_dashboard(db=db, user=_user())

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
        result = await card_dashboard(db=db, user=_user())

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
        result = await card_dashboard(db=db, user=_user())

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

    result = await card_dashboard(db=db, user=_user())

    assert result.projected_annual_rebates == 0.0
    assert result.rebates_this_month_by_status.pending_total == 0.0
    assert result.rebates_this_month_by_status.confirmed_total == 0.0
    assert result.rebates_this_month_by_status.paid_out_total == 0.0
