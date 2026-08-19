"""Tests for the payment_summary endpoint in app/api/payments.py.

DB-free: we mock the tenant DB session.
Key contracts tested:
- The CardRebate query is issued against the TENANT db (card_rebates is a
  per-tenant table, NOT control-plane — querying control_db silently caught
  the missing-table error and always reported $0 rebates).
- When CardRebate raises (table not yet provisioned), total_rebates falls
  back to "0" and db.rollback() is called.
- Money fields serialise as exact Decimal STRINGS (never float) — the
  "Money is exact" invariant; the frontend summary bar parses the string.
- The two money aggregates are resolved into the ORG's reporting currency and
  report what they had to exclude (see `test_payment_summary_currency.py`).

The endpoint issues exactly five tenant-db queries, in order:
paid, pending, payment_count, rebates, queue_count. The first two select a
(sum, excluded_count) PAIR and read it with `.one()`; the last three are
scalars.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_session(*scalar_sequence):
    """Build an AsyncSession mock whose sequential execute() calls each
    return successive values from scalar_sequence.

    The first two calls (paid, pending) select a `(sum, excluded_count)` pair
    and read it with `.one()`; the rest are `.scalar()`. A bare value in the
    sequence is treated as the sum with zero excluded.
    """
    session = AsyncMock()
    results = []
    for val in scalar_sequence:
        if isinstance(val, Exception):
            results.append(val)
        else:
            r = MagicMock()
            r.scalar.return_value = val
            r.one.return_value = (val, 0)
            results.append(r)

    async def execute_side_effect(query):
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    session.execute.side_effect = execute_side_effect
    return session


def _make_user():
    from types import SimpleNamespace

    return SimpleNamespace(id="user-1", roles=["admin"])


def _make_org(settings: dict | None = None):
    from types import SimpleNamespace
    from uuid import uuid4

    return SimpleNamespace(id=uuid4(), name="PyTest", slug="pytesta", settings=settings or {})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_summary_returns_expected_shape():
    """All five fields are present and have the right types."""
    from app.api.payments import payment_summary

    # tenant db, in order: paid=500, pending=200, count=10, rebates=50, queue=3
    db = _make_db_session(Decimal("500.00"), Decimal("200.00"), 10, Decimal("50.00"), 3)

    result = await payment_summary(db=db, org=_make_org(), user=_make_user())

    assert set(result.keys()) == {
        "total_paid",
        "total_pending",
        "payment_count",
        "total_rebates",
        "queue_count",
        "currency",
        "unconverted_payment_count",
    }
    # Money serialises as an exact Decimal STRING (never float) — invariant.
    assert result["total_paid"] == "500.00"
    assert result["total_pending"] == "200.00"
    assert result["payment_count"] == 10
    assert result["total_rebates"] == "50.00"
    assert result["queue_count"] == 3


@pytest.mark.asyncio
async def test_payment_summary_all_zeros_when_no_data():
    """Endpoint handles an empty tenant DB without dividing by zero or raising."""
    from app.api.payments import payment_summary

    db = _make_db_session(None, None, 0, None, 0)

    result = await payment_summary(db=db, org=_make_org(), user=_make_user())

    assert result["total_paid"] == "0"
    assert result["total_pending"] == "0"
    assert result["payment_count"] == 0
    assert result["total_rebates"] == "0"
    assert result["queue_count"] == 0


# ---------------------------------------------------------------------------
# CardRebate query uses the tenant db (regression: it used control_db)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_summary_rebate_query_targets_tenant_db():
    """The rebate sum is the 4th tenant-db query and its value flows through.

    card_rebates is tenant-scoped; querying control_db (the old bug) hit a
    non-existent table, swallowed the error, and always returned $0.
    """
    from app.api.payments import payment_summary

    db = _make_db_session(Decimal("100.00"), Decimal("50.00"), 5, Decimal("12.34"), 2)

    result = await payment_summary(db=db, org=_make_org(), user=_make_user())

    # All five queries went to the tenant db, and the rebate value is real.
    assert db.execute.call_count == 5
    assert result["total_rebates"] == "12.34"


# ---------------------------------------------------------------------------
# CardRebate exception → fallback to 0.0 + rollback on the tenant db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_summary_rebates_fallback_when_table_missing():
    """When the rebate query raises, total_rebates=0.0, db.rollback() is
    called, and the other four fields still come back correctly."""
    from app.api.payments import payment_summary

    # paid, pending, count, [rebate RAISES], queue
    db = _make_db_session(
        Decimal("300.00"),
        Decimal("75.00"),
        7,
        Exception("relation card_rebates does not exist"),
        1,
    )

    result = await payment_summary(db=db, org=_make_org(), user=_make_user())

    assert result["total_rebates"] == "0"
    assert result["total_paid"] == "300.00"
    assert result["total_pending"] == "75.00"
    assert result["payment_count"] == 7
    assert result["queue_count"] == 1
    db.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Money values are exact Decimal STRINGS in the response (never float) —
# the "Money is exact" invariant. A float would round-trip through IEEE-754
# and lose cents; the frontend parses the string.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_summary_returns_decimal_string_not_float():
    from app.api.payments import payment_summary

    db = _make_db_session(Decimal("1.50"), Decimal("2.50"), 3, Decimal("0.75"), 1)

    result = await payment_summary(db=db, org=_make_org(), user=_make_user())

    assert isinstance(result["total_paid"], str)
    assert isinstance(result["total_pending"], str)
    assert isinstance(result["total_rebates"], str)
    assert result["total_paid"] == "1.50"
    assert result["total_pending"] == "2.50"
    assert result["total_rebates"] == "0.75"
