"""Tests for the `card_dashboard` endpoint in `app/api/cards.py`.

We pin the four counters and the derived `projected_annual_rebates`
calculation. The query layer is mocked the same way as
`test_payment_summary` — sequential `execute()` calls feed scalar
values back through `.one()` / `.scalar()`.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_db_session(active_row, *scalars):
    """`active_q` returns a row via `.one()`; the rest return via
    `.scalar()`. Build a session that walks the sequence in order."""
    session = AsyncMock()

    results = []
    one_result = MagicMock()
    one_result.one.return_value = active_row
    results.append(one_result)

    for s in scalars:
        r = MagicMock()
        r.scalar.return_value = s
        results.append(r)

    async def execute_side_effect(_query):
        return results.pop(0)

    session.execute.side_effect = execute_side_effect
    return session


def _user():
    return SimpleNamespace(id="user-1", roles=["admin"])


@pytest.mark.asyncio
async def test_card_dashboard_returns_all_fields():
    """All six fields the frontend rebate panel reads must be present."""
    from app.api.cards import card_dashboard

    db = _make_db_session(
        (3, Decimal("10000.00")),  # active_count, active_value
        Decimal("5000.00"),  # spend_this_month
        Decimal("75.00"),  # rebate_month
        Decimal("400.00"),  # rebate_ytd
    )

    result = await card_dashboard(db=db, user=_user())

    assert result.active_cards == 3
    assert result.active_cards_value == 10000.0
    assert result.spend_this_month == 5000.0
    assert result.rebates_this_month == 75.0
    assert result.rebates_ytd == 400.0
    assert result.projected_annual_rebates > 0


@pytest.mark.asyncio
async def test_projected_annual_extrapolates_from_ytd_when_present():
    """The headline `projected_annual` runs at YTD-pace: 400 YTD in
    month 5 → 400/5 × 12 = 960 annualised."""
    from app.api.cards import card_dashboard

    fixed_now = SimpleNamespace(month=5, year=2026, strftime=lambda fmt: "2026-05")

    db = _make_db_session(
        (0, Decimal("0")),
        Decimal("0"),
        Decimal("80.00"),  # rebate_month — ignored when YTD non-zero
        Decimal("400.00"),  # rebate_ytd
    )

    with patch("app.api.cards.datetime") as mk_dt:
        mk_dt.now.return_value = fixed_now
        result = await card_dashboard(db=db, user=_user())

    assert result.projected_annual_rebates == pytest.approx(960.0)


@pytest.mark.asyncio
async def test_projected_annual_falls_back_to_month_when_ytd_zero():
    """In January when YTD is too short to extrapolate, the dashboard
    uses the more conservative `rebate_month × 12` headline."""
    from app.api.cards import card_dashboard

    fixed_now = SimpleNamespace(month=1, year=2026, strftime=lambda fmt: "2026-01")

    db = _make_db_session(
        (0, Decimal("0")),
        Decimal("0"),
        Decimal("100.00"),  # rebate_month
        Decimal("0"),  # rebate_ytd → fallback path
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
        Decimal("0"),  # rebate_month
        Decimal("0"),  # rebate_ytd
    )

    result = await card_dashboard(db=db, user=_user())

    assert result.projected_annual_rebates == 0.0
