"""Tests for the payment_summary endpoint in app/api/payments.py.

DB-free: we mock both the tenant DB and control DB sessions.
Key contracts tested:
- The CardRebate query is issued against control_db, not db.
- When CardRebate raises (table not yet provisioned), total_rebates falls
  back to 0.0 and control_db.rollback() is called.
- The response shape matches what the frontend summary bar depends on.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_session(*scalar_sequence):
    """Build an AsyncSession mock whose sequential execute() calls each
    return successive values from scalar_sequence via .scalar()."""
    session = AsyncMock()
    results = []
    for val in scalar_sequence:
        if isinstance(val, Exception):
            results.append(val)
        else:
            r = MagicMock()
            r.scalar.return_value = val
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_summary_returns_expected_shape():
    """All five fields are present and have the right types."""
    from app.api.payments import payment_summary

    # tenant db: total_paid=500, total_pending=200, payment_count=10, queue_count=3
    db = _make_db_session(Decimal("500.00"), Decimal("200.00"), 10, 3)
    # control db: total_rebates=50
    control_db = _make_db_session(Decimal("50.00"))

    result = await payment_summary(db=db, control_db=control_db, user=_make_user())

    assert set(result.keys()) == {
        "total_paid",
        "total_pending",
        "payment_count",
        "total_rebates",
        "queue_count",
    }
    assert result["total_paid"] == 500.0
    assert result["total_pending"] == 200.0
    assert result["payment_count"] == 10
    assert result["total_rebates"] == 50.0
    assert result["queue_count"] == 3


@pytest.mark.asyncio
async def test_payment_summary_all_zeros_when_no_data():
    """Endpoint handles an empty tenant DB without dividing by zero or raising."""
    from app.api.payments import payment_summary

    db = _make_db_session(None, None, 0, 0)
    control_db = _make_db_session(None)

    result = await payment_summary(db=db, control_db=control_db, user=_make_user())

    assert result["total_paid"] == 0.0
    assert result["total_pending"] == 0.0
    assert result["payment_count"] == 0
    assert result["total_rebates"] == 0.0
    assert result["queue_count"] == 0


# ---------------------------------------------------------------------------
# CardRebate query uses control_db, not db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_summary_rebate_query_targets_control_db():
    """CardRebate lives in the control plane. Its query must go to control_db.

    We verify this by raising from control_db.execute and confirming
    total_rebates falls back to 0.0 — if the query had gone to db, the
    error would land on a different call index and the assertion would
    fail in an obvious way during debugging.
    """
    from app.api.payments import payment_summary

    db = _make_db_session(Decimal("100.00"), Decimal("50.00"), 5, 2)

    # control_db raises on the first (and only) execute call
    control_db = AsyncMock()
    control_db.execute.side_effect = RuntimeError("table card_rebates does not exist")
    control_db.rollback = AsyncMock()

    result = await payment_summary(db=db, control_db=control_db, user=_make_user())

    # The CardRebate query was sent to control_db
    assert control_db.execute.call_count == 1
    # No CardRebate query reached the tenant db (it should have received
    # exactly its 4 queries: paid, pending, count, queue)
    assert db.execute.call_count == 4

    assert result["total_rebates"] == 0.0


# ---------------------------------------------------------------------------
# CardRebate exception → fallback to 0.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_summary_rebates_fallback_when_table_missing():
    """When control_db raises (e.g. table not provisioned), total_rebates=0.0
    and the endpoint still returns the other four fields correctly."""
    from app.api.payments import payment_summary

    db = _make_db_session(Decimal("300.00"), Decimal("75.00"), 7, 1)

    control_db = AsyncMock()
    control_db.execute.side_effect = Exception("relation card_rebates does not exist")
    control_db.rollback = AsyncMock()

    result = await payment_summary(db=db, control_db=control_db, user=_make_user())

    assert result["total_rebates"] == 0.0
    assert result["total_paid"] == 300.0
    assert result["total_pending"] == 75.0
    assert result["payment_count"] == 7
    assert result["queue_count"] == 1


@pytest.mark.asyncio
async def test_payment_summary_rollback_called_after_rebate_failure():
    """After a CardRebate error, control_db.rollback() must be called so the
    connection is left in a usable state for future requests."""
    from app.api.payments import payment_summary

    db = _make_db_session(Decimal("0"), Decimal("0"), 0, 0)

    control_db = AsyncMock()
    control_db.execute.side_effect = RuntimeError("boom")
    control_db.rollback = AsyncMock()

    await payment_summary(db=db, control_db=control_db, user=_make_user())

    control_db.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Values are floats in the response (not Decimal)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_summary_returns_float_not_decimal():
    """The response dict must contain Python floats so JSON serialisation
    works without a custom encoder — Decimal is not JSON-serialisable."""
    from app.api.payments import payment_summary

    db = _make_db_session(Decimal("1234.56"), Decimal("789.01"), 42, 5)
    control_db = _make_db_session(Decimal("12.34"))

    result = await payment_summary(db=db, control_db=control_db, user=_make_user())

    assert isinstance(result["total_paid"], float)
    assert isinstance(result["total_pending"], float)
    assert isinstance(result["total_rebates"], float)
