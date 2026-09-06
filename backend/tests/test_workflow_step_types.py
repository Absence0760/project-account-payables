"""The step-type vocabulary is owned by ONE module, and the engine fails closed.

Before this file existed, the vocabulary was forked across four places with no
cross-check between them:

  * ``workflow_engine.STEP_TYPES`` — the canonical, order-load-bearing pipeline
  * ``workflow_engine.BUILDER_STEP_TYPES`` — a hand-copied list
  * ``workflow_builder.BUILDER_STEP_TYPES`` — the same list, again
  * ``schemas.workflow.WorkflowStepConfig.type`` — a ``Literal`` naming all nine

`workflow_engine.is_known_step_type` was written to be the shared gate and then
never called from production code, so nothing validated a persisted step type
at all: the import route accepted any string it liked, and the engine resolved a
step number with a bare ``STEP_TYPES.index(resolved)`` that raised
``ValueError: 'condition' is not in list`` — a 500 with no diagnosis — for
exactly the builder types the unused helper existed to recognise.

These tests pin the single source of truth (`services/workflow_step_types.py`),
the typed fail-closed refusal in place of the bare ValueError, and the
definition-save chokepoint that stops an unknown type reaching persistence.
"""

from __future__ import annotations

import typing
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.workflow import WorkflowStepConfig
from app.services import workflow_builder, workflow_engine, workflow_step_types
from app.services.workflow_engine import advance_workflow, create_workflow_step
from app.services.workflow_step_types import (
    BUILDER_STEP_TYPES,
    CANONICAL_STEP_TYPES,
    KNOWN_STEP_TYPES,
    STEP_TYPE_ALIASES,
    NonCanonicalStepTypeError,
    UnknownStepTypeError,
    canonical_step_index,
    is_known_step_type,
    resolve_step_type,
)


def _instance():
    return SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        current_step=0,
        state="active",
        steps_config_snapshot=None,
    )


def _db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# One source of truth — every other copy is the same object, not a twin.
# ---------------------------------------------------------------------------


def test_engine_and_builder_share_the_one_builder_step_type_tuple():
    """Both modules must re-export the SAME object. A copied literal would let
    one grow a type the other rejects, which is how the fork started."""
    assert workflow_engine.BUILDER_STEP_TYPES is BUILDER_STEP_TYPES
    assert workflow_builder.BUILDER_STEP_TYPES is BUILDER_STEP_TYPES
    assert workflow_engine.STEP_TYPES is CANONICAL_STEP_TYPES


def test_known_step_types_is_the_union_of_both_lists():
    assert KNOWN_STEP_TYPES == frozenset(CANONICAL_STEP_TYPES) | frozenset(BUILDER_STEP_TYPES)
    # The canonical order is load-bearing (it resolves a step number) — pin it.
    assert CANONICAL_STEP_TYPES == ("extraction", "approval", "erp_export", "done")
    assert BUILDER_STEP_TYPES == ("condition", "parallel", "webhook", "email", "delay")


def test_request_schema_literal_covers_exactly_the_known_step_types():
    """`WorkflowStepConfig.type` is the API-boundary copy of the vocabulary.
    It stays an explicit Literal (readable, and it lands in the OpenAPI schema),
    so this is its drift guard: a type added to the vocabulary without being
    accepted at the boundary — or vice versa — fails here."""
    literal_args = set(typing.get_args(WorkflowStepConfig.model_fields["type"].annotation))
    assert literal_args == set(KNOWN_STEP_TYPES)


# ---------------------------------------------------------------------------
# resolve / classify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alias,canonical",
    [("upload", "extraction"), ("review", "approval"), ("erp_push", "erp_export")],
)
def test_legacy_aliases_resolve_to_their_canonical_type(alias, canonical):
    assert resolve_step_type(alias) == canonical
    assert is_known_step_type(alias) is True


def test_is_known_step_type_accepts_canonical_builder_and_alias_but_not_junk():
    for t in CANONICAL_STEP_TYPES:
        assert is_known_step_type(t) is True
    for t in BUILDER_STEP_TYPES:
        assert is_known_step_type(t) is True
    assert is_known_step_type("review") is True  # legacy alias → approval
    assert is_known_step_type("not_a_step") is False
    assert is_known_step_type("aproval") is False  # a typo is not a step type
    assert is_known_step_type("") is False
    assert is_known_step_type(None) is False


def test_canonical_step_index_is_one_indexed_and_alias_aware():
    assert canonical_step_index("extraction") == 1
    assert canonical_step_index("review") == 2  # alias
    assert canonical_step_index("done") == 4


def test_canonical_step_index_refuses_a_builder_type_and_an_unknown_type():
    with pytest.raises(NonCanonicalStepTypeError) as builder_exc:
        canonical_step_index("condition")
    # The message has to say WHY, not just that the lookup failed — the bare
    # `.index()` ValueError this replaces read "'condition' is not in list".
    assert "condition" in str(builder_exc.value)
    assert "builder" in str(builder_exc.value).lower()

    with pytest.raises(UnknownStepTypeError):
        canonical_step_index("not_a_step")

    # Both stay ValueErrors so an existing `except ValueError` still catches.
    assert issubclass(NonCanonicalStepTypeError, ValueError)
    assert issubclass(UnknownStepTypeError, ValueError)


def test_no_boolean_canonical_predicate_is_exported():
    """`is_canonical_step_type` was deleted, and must not come back.

    It had no caller and no test, and it answered a strictly worse version of
    the question this module already answers: `False` for BOTH ``"condition"``
    (a recognised builder type in the wrong place) and ``"aproval"`` (not a step
    type at all). Collapsing those two into one bare boolean is precisely the
    silent coercion the module docstring refuses — `canonical_step_index`
    distinguishes them by name (`NonCanonicalStepTypeError` vs
    `UnknownStepTypeError`), and losing that is how a typo'd approval step gets
    read as "no approval step configured".
    """
    assert not hasattr(workflow_step_types, "is_canonical_step_type")


def test_not_in_builder_types_is_exactly_canonical_for_every_known_name():
    """The equivalence the deletion rests on.

    ``workflow_builder.validate_builder_steps`` splits canonical from builder
    steps with ``step_type not in BUILDER_STEP_TYPES``, but only AFTER
    ``is_known_step_type`` has passed. Within that guarded set the shorthand is
    exactly "resolves into CANONICAL_STEP_TYPES" — including for the legacy
    aliases, which are canonical under another name.

    If a future alias or builder type ever made the two disagree, the builder
    would validate a canonical step with a builder validator (or skip the money
    thresholds on an ``approval`` step) — so this pins it rather than leaving it
    to be rediscovered.
    """
    known_names = (
        *CANONICAL_STEP_TYPES,
        *BUILDER_STEP_TYPES,
        *STEP_TYPE_ALIASES,
    )
    for name in known_names:
        assert is_known_step_type(name) is True, name
        shorthand = name not in BUILDER_STEP_TYPES
        resolves_canonical = resolve_step_type(name) in CANONICAL_STEP_TYPES
        assert shorthand is resolves_canonical, name

    # And the shorthand is only ever consulted behind that gate: an unknown
    # name would satisfy it while resolving to nothing, which is why
    # `validate_builder_steps` refuses first and this test asserts second.
    assert is_known_step_type("aproval") is False
    assert resolve_step_type("aproval") not in CANONICAL_STEP_TYPES


# ---------------------------------------------------------------------------
# The engine refuses, in a diagnosable way, instead of raising a bare ValueError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workflow_step_refuses_a_builder_step_type():
    """A builder step is orchestration config, not a leg of the invoice state
    machine — it has no `step_number` and persisting one would corrupt the
    ordering `complete_current_step` relies on. Refuse it by name."""
    db = _db()
    with pytest.raises(NonCanonicalStepTypeError):
        await create_workflow_step(db, _instance(), "condition")
    # Nothing half-persisted on the way out.
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_workflow_step_refuses_an_unknown_step_type():
    db = _db()
    with pytest.raises(UnknownStepTypeError):
        await create_workflow_step(db, _instance(), "not_a_step")
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_advance_workflow_refuses_a_builder_step_type_without_closing_the_current_step():
    """`advance_workflow` used to compute `next_index` with the same bare
    `.index()`, and ran `complete_current_step` FIRST — so the raise left the
    current step CLOSED with no successor opened, a permanently stranded
    instance. The guard must resolve before any mutation, which is what the
    still-open `completed_at` below proves."""
    db = _db()
    inst = _instance()
    open_step = SimpleNamespace(step_number=2, action=None, completed_at=None)
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: open_step))

    with pytest.raises(NonCanonicalStepTypeError):
        await advance_workflow(db, inst, "parallel", action="approved")

    # The step that was open is STILL open — nothing was half-advanced.
    assert open_step.completed_at is None
    assert open_step.action is None
    assert inst.current_step == 0
    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# The definition-save chokepoint — an unknown type never reaches persistence.
# ---------------------------------------------------------------------------


def test_validate_builder_steps_rejects_an_unknown_step_type():
    """`POST /api/workflows/import` takes `steps_config` as a free-form dict —
    the only save path a `Literal` doesn't already constrain. A typo'd
    "aproval" step used to persist and then be silently ignored at runtime,
    quietly dropping the approval gate from the workflow."""
    steps = [
        {"number": 1, "type": "extraction", "name": "Extract", "config": {}},
        {"number": 2, "type": "aproval", "name": "Approve", "config": {}},
    ]
    errors = workflow_builder.validate_builder_steps(steps)
    assert any("aproval" in e for e in errors), errors


def test_validate_builder_steps_still_accepts_canonical_and_builder_types():
    steps = [
        {"number": 1, "type": "extraction", "name": "Extract", "config": {}},
        {"number": 2, "type": "approval", "name": "Approve", "config": {}},
        {
            "number": 3,
            "type": "condition",
            "name": "Big?",
            "config": {"rules": [{"field": "amount", "operator": "gt", "value": 100}]},
        },
        {"number": 4, "type": "done", "name": "Done", "config": {}},
    ]
    assert workflow_builder.validate_builder_steps(steps) == []


def test_validate_builder_steps_rejects_a_missing_step_type():
    errors = workflow_builder.validate_builder_steps([{"number": 1, "name": "Nameless"}])
    assert errors
