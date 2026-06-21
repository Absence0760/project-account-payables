"""Rejecting an invoice must reset multi-level approval-chain state.

`approve_invoice` only initialises chain state when `approval_levels` is
absent. If a rejected-and-reworked invoice kept its old chain state, the next
approval would RESUME at whatever level it was rejected at — silently skipping
every already-satisfied level (a manager→CFO chain rejected at L0 would then
need only the CFO). `reject_invoice` is the single reject chokepoint, so it
clears the chain there; the next approval re-initialises from level 0.

DB-free: the transition, exception creation, instance lookup, and step
completion are mocked, mirroring `test_review_approval.py`.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _make_invoice():
    return SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        rejected_by=None,
    )


def _instance(state_data):
    return SimpleNamespace(id=uuid.uuid4(), state_data=state_data)


@pytest.mark.asyncio
async def test_reject_clears_approval_levels_and_bumps_rejection_count():
    from app.services import review

    invoice = _make_invoice()
    # An invoice mid-chain: the chain advanced past level 0.
    instance = _instance(
        {
            "approval_levels": {
                "levels": [
                    {"level": 0, "approvals": [{"user_id": "u1"}]},
                    {"level": 1, "approvals": []},
                ],
                "current_level": 1,
            },
            "rejection_count": 0,
        }
    )

    db = AsyncMock()
    with (
        patch.object(review, "transition_invoice", new=AsyncMock()),
        patch.object(review, "get_workflow_instance", new=AsyncMock(return_value=instance)),
        patch.object(review, "complete_current_step", new=AsyncMock()),
        patch("app.services.exception_service.create_exception", new=AsyncMock()),
    ):
        await review.reject_invoice(
            db,
            invoice,
            actor_id=uuid.uuid4(),
            actor_name="Rejector",
            reason="Wrong GL coding",
        )

    # Chain state is wiped so the next approval re-initialises from level 0…
    assert "approval_levels" not in instance.state_data
    # …and the rejection is counted.
    assert instance.state_data["rejection_count"] == 1


@pytest.mark.asyncio
async def test_reject_with_no_chain_state_is_safe():
    """An invoice that never had chain state rejects cleanly (pop is a no-op)."""
    from app.services import review

    invoice = _make_invoice()
    instance = _instance({})

    db = AsyncMock()
    with (
        patch.object(review, "transition_invoice", new=AsyncMock()),
        patch.object(review, "get_workflow_instance", new=AsyncMock(return_value=instance)),
        patch.object(review, "complete_current_step", new=AsyncMock()),
        patch("app.services.exception_service.create_exception", new=AsyncMock()),
    ):
        await review.reject_invoice(
            db,
            invoice,
            actor_id=uuid.uuid4(),
            actor_name="Rejector",
            reason="Duplicate",
        )

    assert "approval_levels" not in instance.state_data
    assert instance.state_data["rejection_count"] == 1
