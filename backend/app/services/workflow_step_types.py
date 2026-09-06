"""The workflow step-type vocabulary — one source of truth for all of it.

Two disjoint families of step type live in the same ``steps_config`` JSONB:

``CANONICAL_STEP_TYPES``
    The linear pipeline that drives the invoice state machine. **Its order is
    load-bearing** — a ``WorkflowStep.step_number`` is this tuple's 1-based
    index, and ``complete_current_step`` finds the open step by ordering on
    that number. Nothing may be appended, reordered, or removed without a data
    migration for the rows already carrying those numbers.

``BUILDER_STEP_TYPES``
    The no-code builder's orchestration types (``services/workflow_builder``).
    They branch / fan out / notify but never advance the invoice state machine,
    so they deliberately have **no** step number and are never persisted as a
    ``WorkflowStep``.

Why this module exists: the two lists used to be hand-copied into
``workflow_engine`` and ``workflow_builder`` with no cross-check, plus a third
copy as the ``Literal`` on ``schemas.workflow.WorkflowStepConfig.type``.
``workflow_engine.is_known_step_type`` was written to be the shared gate and
never called from production, so nothing validated a persisted step type at
all — and the engine resolved a step number with a bare ``.index()`` that
raised ``ValueError: 'condition' is not in list``, a 500 naming no cause, for
exactly the builder types that helper existed to recognise.

The posture matches ``decisions §29`` (a mis-typed provider name never resolves
to the fixture adapter): a step type we do not recognise is **refused by name**,
never quietly coerced into something plausible. Silently ignoring an unknown
type is the dangerous outcome here — a typo'd ``"aproval"`` step reads as "no
approval step configured", which drops the approval gate off the workflow.

Pure module: stdlib only, no ORM, no IO. Import it from anywhere.
"""

from __future__ import annotations

# The canonical linear pipeline. ORDER IS LOAD-BEARING (see module docstring).
CANONICAL_STEP_TYPES: tuple[str, ...] = ("extraction", "approval", "erp_export", "done")

# The no-code builder's orchestration types — config-only, never a WorkflowStep.
BUILDER_STEP_TYPES: tuple[str, ...] = ("condition", "parallel", "webhook", "email", "delay")

# Everything a persisted `steps_config` may legally name.
KNOWN_STEP_TYPES: frozenset[str] = frozenset(CANONICAL_STEP_TYPES) | frozenset(BUILDER_STEP_TYPES)

# Backwards-compatible aliases for old step type names. Callers still pass
# these (`api/workflow.py` and `services/review.py` both do), and rows written
# before the rename carry the canonical name — `create_workflow_step` resolves
# before persisting so a query filtering on "approval" can't miss an alias row.
STEP_TYPE_ALIASES: dict[str, str] = {
    "upload": "extraction",
    "review": "approval",
    "erp_push": "erp_export",
}


class UnknownStepTypeError(ValueError):
    """A step type that is neither canonical, a builder type, nor a legacy alias.

    Subclasses ``ValueError`` so an existing ``except ValueError`` around the
    old bare ``.index()`` still catches it.
    """


class NonCanonicalStepTypeError(ValueError):
    """A *recognised* builder step type used where a pipeline step is required.

    Distinct from :class:`UnknownStepTypeError` because the two mean opposite
    things to whoever reads the log: this one says the workflow config is fine
    and the caller asked the wrong question; the other says the config itself
    names something that does not exist.
    """


def resolve_step_type(step_type: str | None) -> str:
    """Map a legacy alias onto its canonical name; pass everything else through.

    Never raises — classification is :func:`is_known_step_type`'s job.
    """
    if not step_type:
        return ""
    return STEP_TYPE_ALIASES.get(step_type, step_type)


def is_known_step_type(step_type: str | None) -> bool:
    """True if ``step_type`` is a canonical pipeline step, a legacy alias, or a
    no-code builder step type.

    This is the gate the definition-save chokepoint runs so an unrecognised type
    can never reach persistence (and therefore can never reach the engine).
    """
    return resolve_step_type(step_type) in KNOWN_STEP_TYPES


# There is deliberately NO boolean `is_canonical_step_type` here. It existed,
# had no caller and no test, and it collapsed the one distinction this module
# was written to draw: it answers `False` for both `"condition"` (a recognised
# builder type used in the wrong place) and `"aproval"` (a typo that is not a
# step type at all), which is exactly the silent coercion the module docstring
# refuses. `canonical_step_index` is the canonical-ness question, and it
# answers it BY NAME — `NonCanonicalStepTypeError` vs `UnknownStepTypeError`.
# Where a plain predicate really is enough, `workflow_builder` asks the inverse
# after `is_known_step_type` has already passed (`step_type not in
# BUILDER_STEP_TYPES`), which `tests/test_workflow_step_types.py` pins as
# equivalent to "resolves into CANONICAL_STEP_TYPES" for every known name.


def canonical_step_index(step_type: str | None) -> int:
    """The 1-based ``WorkflowStep.step_number`` for a canonical pipeline step.

    Raises :class:`NonCanonicalStepTypeError` for a builder type and
    :class:`UnknownStepTypeError` for anything else — both naming the offending
    value, so the failure is diagnosable from one log line. This replaces the
    bare ``STEP_TYPES.index(...)`` whose ``ValueError`` said only
    ``list.index(x): x not in list``.
    """
    resolved = resolve_step_type(step_type)
    if resolved in CANONICAL_STEP_TYPES:
        return CANONICAL_STEP_TYPES.index(resolved) + 1
    if resolved in BUILDER_STEP_TYPES:
        raise NonCanonicalStepTypeError(
            f"step type {resolved!r} is a no-code builder step, which orchestrates "
            "config only and has no place in the invoice pipeline — it is never "
            "persisted as a workflow step"
        )
    raise UnknownStepTypeError(
        f"unknown workflow step type {step_type!r}; expected one of "
        f"{sorted(KNOWN_STEP_TYPES)} (or a legacy alias {sorted(STEP_TYPE_ALIASES)})"
    )
