"""Unit tests for _enforce_approval_thresholds in services.review.

Tests the max_invoice_amount cap and the CFO role gate. The workflow
instance and its DB query are mocked so no running Postgres is needed.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_invoice(amount: float):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        amount=Decimal(str(amount)),
    )


def _make_instance(approval_config: dict | None):
    """Build a WorkflowInstance-like object with the given approval step config."""
    if approval_config is None:
        snapshot = None
    else:
        snapshot = {
            "steps": [
                {
                    "type": "approval",
                    "config": approval_config,
                }
            ]
        }
    return SimpleNamespace(
        id=uuid.uuid4(),
        steps_config_snapshot=snapshot,
    )


def _db_mock():
    return AsyncMock()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_amount_rejects():
    """Invoice with amount=50000 and max_invoice_amount=10000 raises 422."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    invoice = _make_invoice(amount=50000)
    instance = _make_instance({"max_invoice_amount": 10000})

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=instance)
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _enforce_approval_thresholds(db, invoice, actor_roles=set())

    assert exc_info.value.status_code == 422
    assert "50,000" in exc_info.value.detail
    assert "10,000" in exc_info.value.detail


@pytest.mark.asyncio
async def test_max_amount_allows_under():
    """Invoice with amount=5000 under max_invoice_amount=10000 passes without error."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    invoice = _make_invoice(amount=5000)
    instance = _make_instance({"max_invoice_amount": 10000})

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=instance)
    ):
        # Must not raise
        await _enforce_approval_thresholds(db, invoice, actor_roles=set())


@pytest.mark.asyncio
async def test_max_amount_allows_exact_limit():
    """Invoice amount exactly equal to max_invoice_amount is allowed."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    invoice = _make_invoice(amount=10000)
    instance = _make_instance({"max_invoice_amount": 10000})

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=instance)
    ):
        # Must not raise
        await _enforce_approval_thresholds(db, invoice, actor_roles=set())


@pytest.mark.asyncio
async def test_cfo_required_blocks_non_cfo():
    """Amount above require_cfo_above threshold with non-CFO role raises 403."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    invoice = _make_invoice(amount=50000)
    instance = _make_instance({"require_cfo_above": 10000})

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=instance)
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _enforce_approval_thresholds(db, invoice, actor_roles={"ap_manager"})

    assert exc_info.value.status_code == 403
    assert "CFO" in exc_info.value.detail


@pytest.mark.asyncio
async def test_cfo_required_allows_cfo():
    """Amount above require_cfo_above threshold passes when actor has the cfo role."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    invoice = _make_invoice(amount=50000)
    instance = _make_instance({"require_cfo_above": 10000})

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=instance)
    ):
        # Must not raise
        await _enforce_approval_thresholds(db, invoice, actor_roles={"cfo"})


@pytest.mark.asyncio
async def test_cfo_required_allows_amount_at_or_below_threshold():
    """An invoice at or below require_cfo_above is not blocked, even without the cfo role."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    invoice = _make_invoice(amount=10000)
    instance = _make_instance({"require_cfo_above": 10000})

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=instance)
    ):
        # Exactly at threshold — must not raise
        await _enforce_approval_thresholds(db, invoice, actor_roles={"ap_manager"})


@pytest.mark.asyncio
async def test_no_config_passes():
    """An empty approval step config imposes no restrictions."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    invoice = _make_invoice(amount=999999)
    instance = _make_instance({})  # empty config

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=instance)
    ):
        # Must not raise
        await _enforce_approval_thresholds(db, invoice, actor_roles=set())


@pytest.mark.asyncio
async def test_no_instance_passes():
    """When get_workflow_instance returns None the function is a no-op."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    invoice = _make_invoice(amount=999999)

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=None)
    ):
        # Must not raise
        await _enforce_approval_thresholds(db, invoice, actor_roles=set())


@pytest.mark.asyncio
async def test_instance_with_no_snapshot_passes():
    """An instance whose steps_config_snapshot is None is treated as unconfigured."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    invoice = _make_invoice(amount=999999)
    instance = SimpleNamespace(id=uuid.uuid4(), steps_config_snapshot=None)

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=instance)
    ):
        # Must not raise
        await _enforce_approval_thresholds(db, invoice, actor_roles=set())


@pytest.mark.asyncio
async def test_both_max_amount_and_cfo_gate_enforced():
    """When both limits are configured, max_invoice_amount is checked first (422 wins)."""
    from app.services.review import _enforce_approval_thresholds

    db = _db_mock()
    # Amount violates max_invoice_amount AND lacks cfo role
    invoice = _make_invoice(amount=100000)
    instance = _make_instance({"max_invoice_amount": 10000, "require_cfo_above": 5000})

    with patch(
        "app.services.review.get_workflow_instance", new=AsyncMock(return_value=instance)
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _enforce_approval_thresholds(db, invoice, actor_roles={"ap_manager"})

    # max_invoice_amount check comes first in the source, so we expect 422
    assert exc_info.value.status_code == 422
