"""Workflow orchestration helpers — instance + step lifecycle.

`test_workflow_state_machine.py` pins the VALID_TRANSITIONS structure
and the snapshot column. `test_workflow_snapshot_runtime.py` pins
that runtime reads the snapshot. THIS file pins the orchestration
helpers that move work between steps:

  - `create_workflow_instance` — copies the live `steps_config` into
    `steps_config_snapshot` at exactly one moment (invoice creation);
    later edits to the definition don't touch the in-flight instance
  - `create_workflow_step` — translates the canonical step type
    (and legacy aliases like `upload`/`review`/`erp_push`) into a
    `step_number` that matches STEP_TYPES
  - `complete_current_step` — marks the most recent incomplete step
    as `completed_at=now()` with the supplied action; idempotent if
    no incomplete step exists (no crash)
  - `advance_workflow` — closes the current step and opens the next,
    bumping `instance.current_step` to the next index
  - `complete_workflow` — closes the current step, creates a `done`
    sentinel step, sets `instance.state="completed"`

A regression in step-number ordering breaks the queue builder (steps
render out of order); a regression in the snapshot copy breaks
deterministic replay for SOC 2 audits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.workflow_engine import (
    DEFAULT_STEPS_CONFIG,
    STEP_TYPES,
    advance_workflow,
    complete_current_step,
    complete_workflow,
    create_workflow_instance,
    create_workflow_step,
)


def _invoice():
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        # create_workflow_instance resolves the entity's workflow definition
        # (per-entity selection, Phase 3); a real Invoice always carries this.
        entity_id=uuid.uuid4(),
    )


def _instance(*, current_step=0, state="active"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        current_step=current_step,
        state=state,
        steps_config_snapshot=None,
    )


def _step(*, step_number=2, completed=None, action=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        step_number=step_number,
        action=action,
        completed_at=completed,
    )


# ---------------------------------------------------------------------------
# create_workflow_instance — the snapshot is taken at this exact moment.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workflow_instance_snapshots_the_live_definition_at_creation():
    """The `steps_config_snapshot` MUST be a copy of whatever
    `defn.steps_config` is right now. Mutating the live definition
    afterwards must NOT leak into the snapshot (that's the whole
    point of snapshotting)."""
    live = {"steps": [{"type": "approval", "enabled": True}]}
    defn = SimpleNamespace(id=uuid.uuid4(), steps_config=live)
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    inv = _invoice()

    with patch(
        "app.services.workflow_engine.get_or_create_workflow_definition",
        AsyncMock(return_value=defn),
    ):
        instance = await create_workflow_instance(db, inv)

    # The snapshot equals the live definition at this moment.
    assert instance.steps_config_snapshot == live
    assert instance.invoice_id == inv.id
    assert instance.correlation_id == inv.correlation_id
    assert instance.current_step == 0
    assert instance.state == "active"
    # The instance is added to the session and flushed (so .id is
    # available to callers immediately).
    db.add.assert_called_once_with(instance)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_workflow_instance_uses_default_config_when_definition_is_default():
    """Fresh tenant — `get_or_create_workflow_definition` returns a
    new definition seeded from `DEFAULT_STEPS_CONFIG`. The snapshot
    matches that shape (all steps disabled, opt-in)."""
    defn = SimpleNamespace(id=uuid.uuid4(), steps_config=DEFAULT_STEPS_CONFIG)
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch(
        "app.services.workflow_engine.get_or_create_workflow_definition",
        AsyncMock(return_value=defn),
    ):
        instance = await create_workflow_instance(db, _invoice())

    # All three steps disabled by default.
    for step in instance.steps_config_snapshot["steps"]:
        assert step["enabled"] is False


# ---------------------------------------------------------------------------
# create_workflow_step — step_number derivation + alias handling.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workflow_step_uses_canonical_step_number_from_step_types():
    """`step_number` is 1-indexed against the STEP_TYPES list. A
    regression that returned 0-indexed would order the queue wrong
    in the UI."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    inst = _instance()

    for idx, step_type in enumerate(STEP_TYPES, start=1):
        step = await create_workflow_step(db, inst, step_type)
        assert step.step_number == idx, f"step_number for {step_type} should be {idx}"


@pytest.mark.parametrize(
    "alias,canonical_number",
    [
        ("upload", 1),  # alias for extraction
        ("review", 2),  # alias for approval
        ("erp_push", 3),  # alias for erp_export
    ],
)
@pytest.mark.asyncio
async def test_create_workflow_step_honors_legacy_aliases(alias, canonical_number):
    """Old workflow definitions used `upload`/`review`/`erp_push`;
    the new names are `extraction`/`approval`/`erp_export`. The
    helper translates so callers can still pass either. A regression
    here orphans old WorkflowSteps from their definitions."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    inst = _instance()

    step = await create_workflow_step(db, inst, alias)
    # step_type field is kept verbatim (so the old data round-trips
    # without rewriting), but the step_number is canonicalized.
    assert step.step_type == alias
    assert step.step_number == canonical_number


@pytest.mark.asyncio
async def test_create_workflow_step_carries_correlation_id_and_assignee():
    """The step row must carry `correlation_id` (for replay
    queries) and `assigned_to` when provided."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    inst = _instance()
    assignee = uuid.uuid4()

    step = await create_workflow_step(db, inst, "approval", assigned_to=assignee)

    assert step.correlation_id == inst.correlation_id
    assert step.assigned_to == assignee
    assert step.instance_id == inst.id


# ---------------------------------------------------------------------------
# complete_current_step — closes the most recent incomplete step.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_current_step_marks_action_and_completed_at_on_open_step():
    """Most recent incomplete step → set its action + completed_at.
    A regression that grabbed any step (including already-completed
    ones) would clobber audit trails."""
    open_step = _step(step_number=2)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=open_step)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    inst = _instance()

    before = datetime.now(UTC)
    step = await complete_current_step(db, inst, action="approved")
    after = datetime.now(UTC)

    assert step is open_step
    assert step.action == "approved"
    assert step.completed_at is not None
    assert before <= step.completed_at <= after


@pytest.mark.asyncio
async def test_complete_current_step_is_no_op_when_no_incomplete_step():
    """If the query returns None (every step is already done), the
    helper must return None without raising. This is the path when
    `complete_workflow` is called twice in a row — it shouldn't
    crash."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    out = await complete_current_step(db, _instance(), action="anything")
    assert out is None


# ---------------------------------------------------------------------------
# advance_workflow — close + open in one shot, bumps current_step.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_workflow_closes_current_and_opens_next_step():
    """`advance_workflow` is the standard step pump: close the
    currently-open step with the given action, create the next step
    of the requested type, bump instance.current_step to the index
    of that next step."""
    open_step = _step(step_number=1)
    close_result = MagicMock()
    close_result.scalar_one_or_none = MagicMock(return_value=open_step)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=close_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    inst = _instance(current_step=0)

    new_step = await advance_workflow(db, inst, "approval", action="extracted")

    # Old step now closed with the action.
    assert open_step.action == "extracted"
    assert open_step.completed_at is not None
    # Instance points at the new step's index.
    assert inst.current_step == STEP_TYPES.index("approval")
    # New step has the right type and step_number.
    assert new_step.step_type == "approval"
    assert new_step.step_number == STEP_TYPES.index("approval") + 1


@pytest.mark.asyncio
async def test_advance_workflow_honors_legacy_alias_in_next_step_type():
    """Caller passes `review` (legacy alias for `approval`). The
    step row records `review` verbatim but the instance.current_step
    is computed via the canonical name (so the index is right)."""
    close_result = MagicMock()
    close_result.scalar_one_or_none = MagicMock(return_value=None)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=close_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    inst = _instance(current_step=0)

    await advance_workflow(db, inst, "review", action="extracted")

    assert inst.current_step == STEP_TYPES.index("approval")


# ---------------------------------------------------------------------------
# complete_workflow — terminal action.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_workflow_creates_done_sentinel_and_marks_instance_completed():
    """`complete_workflow` is the terminal hook: close the current
    step, create a `done` step already marked complete, set
    `instance.current_step` to the last STEP_TYPES index and
    `instance.state = "completed"`."""
    open_step = _step(step_number=3)
    close_result = MagicMock()
    close_result.scalar_one_or_none = MagicMock(return_value=open_step)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=close_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    inst = _instance(current_step=2)

    await complete_workflow(db, inst, action="erp_confirmed")

    # The previously-open step closed with the action.
    assert open_step.action == "erp_confirmed"
    assert open_step.completed_at is not None
    # A new sentinel step of type "done" was added with action
    # "completed" and a completed_at timestamp — captured via the
    # add() call.
    added = [c.args[0] for c in db.add.call_args_list]
    done_step = next(s for s in added if getattr(s, "step_type", None) == "done")
    assert done_step.action == "completed"
    assert done_step.completed_at is not None
    # Instance is now at the last index, in completed state.
    assert inst.current_step == len(STEP_TYPES) - 1
    assert inst.state == "completed"
