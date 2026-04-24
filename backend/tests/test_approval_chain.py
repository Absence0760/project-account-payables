"""Unit tests for the approval chain service.

Covers segregation-of-duties enforcement, delegate resolution,
amount-based level filtering, chain state initialisation, and
approval advancement. All tests are DB-free — DB sessions are
replaced with AsyncMock so no running Postgres is required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_invoice(*, uploaded_by_id=None, amount=None):
    """Return a minimal Invoice-like object for use in segregation tests."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        uploaded_by_id=uploaded_by_id,
        amount=amount,
    )


def _make_instance(*, state_data=None, steps_config_snapshot=None):
    """Return a minimal WorkflowInstance-like object."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        state_data=state_data,
        steps_config_snapshot=steps_config_snapshot,
    )


def _make_user(*, delegate_to_id=None, delegate_until=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        delegate_to_id=delegate_to_id,
        delegate_until=delegate_until,
    )


def _db_returning(scalar_value):
    """Build an AsyncMock session whose execute() returns scalar_value."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=scalar_value)
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# check_segregation
# ---------------------------------------------------------------------------


def test_segregation_blocks_uploader_from_approving():
    """Uploader matching actor_id with require_segregation=True raises 403."""
    from app.services.approval_chain import check_segregation

    actor_id = uuid.uuid4()
    invoice = _make_invoice(uploaded_by_id=actor_id)
    config = {"require_segregation": True}

    with pytest.raises(HTTPException) as exc_info:
        check_segregation(invoice, actor_id, config)

    assert exc_info.value.status_code == 403
    assert "segregation" in exc_info.value.detail.lower()


def test_segregation_allows_different_user():
    """A different user from the uploader is never blocked, even with require_segregation=True."""
    from app.services.approval_chain import check_segregation

    uploader_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    invoice = _make_invoice(uploaded_by_id=uploader_id)
    config = {"require_segregation": True}

    # Must not raise
    check_segregation(invoice, actor_id, config)


def test_segregation_disabled_allows_uploader():
    """When require_segregation is False the uploader may approve their own invoice."""
    from app.services.approval_chain import check_segregation

    actor_id = uuid.uuid4()
    invoice = _make_invoice(uploaded_by_id=actor_id)
    config = {"require_segregation": False}

    # Must not raise
    check_segregation(invoice, actor_id, config)


def test_segregation_skips_null_uploaded_by():
    """uploaded_by_id=None (pre-existing invoice) skips the check entirely."""
    from app.services.approval_chain import check_segregation

    actor_id = uuid.uuid4()
    invoice = _make_invoice(uploaded_by_id=None)
    config = {"require_segregation": True}

    # Must not raise even though require_segregation is True
    check_segregation(invoice, actor_id, config)


def test_segregation_defaults_on_when_key_missing():
    """Empty approval config (no require_segregation key) still blocks the uploader.

    SoD is default-on as of the SOC 2 baseline pass — orgs must explicitly
    opt out by setting the key to False.
    """
    from app.services.approval_chain import check_segregation

    actor_id = uuid.uuid4()
    invoice = _make_invoice(uploaded_by_id=actor_id)
    config: dict = {}  # no require_segregation key

    with pytest.raises(HTTPException) as exc_info:
        check_segregation(invoice, actor_id, config)

    assert exc_info.value.status_code == 403


def test_default_steps_config_has_segregation_enabled():
    """New workflow definitions default to require_segregation=True on the approval step."""
    from app.services.workflow_engine import DEFAULT_STEPS_CONFIG

    approval = next(s for s in DEFAULT_STEPS_CONFIG["steps"] if s["type"] == "approval")
    assert approval["config"].get("require_segregation") is True


def test_approval_step_schema_defaults_segregation_on():
    """The Pydantic schema defaults require_segregation to True."""
    from app.schemas.workflow import ApprovalStepConfig

    cfg = ApprovalStepConfig()
    assert cfg.require_segregation is True


# ---------------------------------------------------------------------------
# resolve_assignee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_active():
    """When delegate_to_id is set and delegate_until is in the future, returns the delegate."""
    from app.services.approval_chain import resolve_assignee

    delegate_id = uuid.uuid4()
    user_id = uuid.uuid4()
    user = _make_user(
        delegate_to_id=delegate_id,
        delegate_until=datetime.now(UTC) + timedelta(days=1),
    )
    user.id = user_id

    db = _db_returning(user)
    effective_id, original_id = await resolve_assignee(user_id, db)

    assert effective_id == delegate_id
    assert original_id == user_id


@pytest.mark.asyncio
async def test_delegation_expired():
    """When delegate_until is in the past, returns the original user without delegation."""
    from app.services.approval_chain import resolve_assignee

    delegate_id = uuid.uuid4()
    user_id = uuid.uuid4()
    user = _make_user(
        delegate_to_id=delegate_id,
        delegate_until=datetime.now(UTC) - timedelta(seconds=1),
    )
    user.id = user_id

    db = _db_returning(user)
    effective_id, original_id = await resolve_assignee(user_id, db)

    assert effective_id == user_id
    assert original_id is None


@pytest.mark.asyncio
async def test_no_delegation():
    """When delegate_to_id is None the function returns the original user unchanged."""
    from app.services.approval_chain import resolve_assignee

    user_id = uuid.uuid4()
    user = _make_user(delegate_to_id=None, delegate_until=None)
    user.id = user_id

    db = _db_returning(user)
    effective_id, original_id = await resolve_assignee(user_id, db)

    assert effective_id == user_id
    assert original_id is None


@pytest.mark.asyncio
async def test_delegation_user_not_found():
    """When the user_id does not exist in the DB, returns (user_id, None) without raising."""
    from app.services.approval_chain import resolve_assignee

    user_id = uuid.uuid4()
    db = _db_returning(None)  # scalar returns None

    effective_id, original_id = await resolve_assignee(user_id, db)

    assert effective_id == user_id
    assert original_id is None


# ---------------------------------------------------------------------------
# resolve_applicable_levels
# ---------------------------------------------------------------------------


def test_all_levels_apply_unbounded():
    """Levels with no amount bounds always apply, regardless of invoice amount."""
    from app.services.approval_chain import resolve_applicable_levels

    chain = [
        {"name": "L1"},
        {"name": "L2"},
    ]
    result = resolve_applicable_levels(chain, amount=99999)
    assert len(result) == 2
    assert result[0]["name"] == "L1"
    assert result[1]["name"] == "L2"


def test_level_filtered_by_min_amount():
    """A level with min_amount=500 is excluded when amount=100."""
    from app.services.approval_chain import resolve_applicable_levels

    chain = [{"name": "Manager", "min_amount": 500}]
    result = resolve_applicable_levels(chain, amount=100)
    assert result == []


def test_level_filtered_by_max_amount():
    """A level with max_amount=500 is excluded when amount=1000."""
    from app.services.approval_chain import resolve_applicable_levels

    chain = [{"name": "Clerk", "max_amount": 500}]
    result = resolve_applicable_levels(chain, amount=1000)
    assert result == []


def test_level_within_range():
    """A level with min=100 and max=500 is included when amount=300."""
    from app.services.approval_chain import resolve_applicable_levels

    chain = [{"name": "Manager", "min_amount": 100, "max_amount": 500}]
    result = resolve_applicable_levels(chain, amount=300)
    assert len(result) == 1
    assert result[0]["name"] == "Manager"


def test_level_at_exact_boundary_is_included():
    """Boundary values (amount == min or amount == max) are inclusive."""
    from app.services.approval_chain import resolve_applicable_levels

    chain = [{"name": "Exact", "min_amount": 100, "max_amount": 500}]

    assert len(resolve_applicable_levels(chain, amount=100)) == 1
    assert len(resolve_applicable_levels(chain, amount=500)) == 1


def test_empty_chain():
    """An empty chain returns an empty result for any amount."""
    from app.services.approval_chain import resolve_applicable_levels

    assert resolve_applicable_levels([], amount=12345) == []


# ---------------------------------------------------------------------------
# init_chain_state
# ---------------------------------------------------------------------------


def test_init_creates_level_structure():
    """After init, state_data contains a well-formed approval_levels dict."""
    from app.services.approval_chain import init_chain_state

    instance = _make_instance(state_data=None)
    levels = [
        {"name": "Manager", "required_approvals": 1, "approver_ids": ["uid-a"]},
        {"name": "CFO", "required_approvals": 2, "approver_ids": ["uid-b", "uid-c"]},
    ]

    init_chain_state(instance, levels)

    chain = instance.state_data["approval_levels"]
    assert chain["current_level"] == 0
    assert len(chain["levels"]) == 2

    first = chain["levels"][0]
    assert first["level"] == 0
    assert first["name"] == "Manager"
    assert first["required"] == 1
    assert first["approver_ids"] == ["uid-a"]
    assert first["approvals"] == []

    second = chain["levels"][1]
    assert second["level"] == 1
    assert second["name"] == "CFO"
    assert second["required"] == 2


def test_init_preserves_existing_state_data():
    """Initialising chain state does not erase unrelated keys like rejection_count."""
    from app.services.approval_chain import init_chain_state

    instance = _make_instance(state_data={"rejection_count": 3, "other_key": "value"})
    levels = [{"name": "L1", "required_approvals": 1, "approver_ids": []}]

    init_chain_state(instance, levels)

    assert instance.state_data["rejection_count"] == 3
    assert instance.state_data["other_key"] == "value"
    assert "approval_levels" in instance.state_data


# ---------------------------------------------------------------------------
# advance_approval_chain
# ---------------------------------------------------------------------------


def test_first_approval_on_single_level_completes():
    """A single-level chain with required=1 returns True on the first approval."""
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    instance = _make_instance(state_data=None)
    init_chain_state(instance, [{"name": "L1", "required_approvals": 1, "approver_ids": []}])

    complete = advance_approval_chain(instance, uuid.uuid4())

    assert complete is True


def test_first_approval_on_multi_level_advances():
    """First approval on a 2-level chain advances to level 1 but returns False."""
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    instance = _make_instance(state_data=None)
    init_chain_state(
        instance,
        [
            {"name": "L1", "required_approvals": 1, "approver_ids": []},
            {"name": "L2", "required_approvals": 1, "approver_ids": []},
        ],
    )

    complete = advance_approval_chain(instance, uuid.uuid4())

    assert complete is False
    assert instance.state_data["approval_levels"]["current_level"] == 1


def test_second_approval_on_multi_level_completes():
    """After advancing to level 1, the second approval (on level 1) returns True."""
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    instance = _make_instance(state_data=None)
    init_chain_state(
        instance,
        [
            {"name": "L1", "required_approvals": 1, "approver_ids": []},
            {"name": "L2", "required_approvals": 1, "approver_ids": []},
        ],
    )

    # First approval advances to level 1
    advance_approval_chain(instance, uuid.uuid4())
    # Second approval completes the chain
    complete = advance_approval_chain(instance, uuid.uuid4())

    assert complete is True


def test_multiple_approvals_required():
    """A level that requires 2 approvals returns False on the first, True on the second."""
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    instance = _make_instance(state_data=None)
    init_chain_state(instance, [{"name": "Dual", "required_approvals": 2, "approver_ids": []}])

    first_result = advance_approval_chain(instance, uuid.uuid4())
    assert first_result is False

    second_result = advance_approval_chain(instance, uuid.uuid4())
    assert second_result is True


def test_empty_chain_state_returns_true():
    """An instance with no approval_levels in state_data is treated as complete."""
    from app.services.approval_chain import advance_approval_chain

    instance = _make_instance(state_data={})

    complete = advance_approval_chain(instance, uuid.uuid4())

    assert complete is True


def test_advance_records_actor_id_in_approvals():
    """Each call to advance records the actor_id on the current level's approvals list."""
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    instance = _make_instance(state_data=None)
    init_chain_state(instance, [{"name": "L1", "required_approvals": 2, "approver_ids": []}])

    actor = uuid.uuid4()
    advance_approval_chain(instance, actor)

    approvals = instance.state_data["approval_levels"]["levels"][0]["approvals"]
    assert len(approvals) == 1
    assert approvals[0]["user_id"] == str(actor)
    assert "at" in approvals[0]
