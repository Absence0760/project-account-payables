"""Workflow snapshot semantics — pins the rule that runtime reads the
frozen per-invoice snapshot, *not* the live `WorkflowDefinition`.

The snapshot pattern exists because editing a definition (toggling
extraction on, raising the auto-approve threshold, etc.) must NOT
re-shape invoices that are already mid-flight. The whole point is
deterministic replay for compliance — every audit row should reflect
the rules that were in force *when the invoice was created*, not
whatever the admin tweaked since.

These tests pin:
  - `is_step_enabled(..., invoice_id=...)` reads the instance's
    snapshot when one exists, even if the live definition would say
    otherwise.
  - `is_step_enabled(..., invoice_id=None)` falls back to the live
    definition (the path used during invoice creation, before any
    instance exists).
  - `is_step_enabled(..., invoice_id=X)` falls back to the live def
    when the instance is missing or has a null snapshot (legacy /
    backfill case).
  - `_check_step_enabled` defaults to enabled=True when the step type
    is absent from the config (forward-compat).
  - `get_step_config` returns the per-step config dict so callers can
    read auto-approve thresholds, approver IDs, etc., without
    re-fetching the live definition.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.workflow_engine import (
    DEFAULT_STEPS_CONFIG,
    _check_step_enabled,
    get_step_config,
    is_step_enabled,
)


def _snapshot(**overrides: bool) -> dict:
    """Build a steps_config dict with explicit enabled flags per step."""
    return {
        "steps": [
            {"number": 1, "type": "extraction", "enabled": overrides.get("extraction", False)},
            {"number": 2, "type": "approval", "enabled": overrides.get("approval", False),
             "config": {"required": True, "approver_id": None}},
            {"number": 3, "type": "erp_export", "enabled": overrides.get("erp_export", False)},
        ]
    }


@pytest.mark.asyncio
async def test_is_step_enabled_reads_snapshot_when_invoice_provided():
    """Snapshot says approval is enabled; live def says it isn't.
    With an invoice_id, runtime MUST honor the snapshot."""
    db = AsyncMock()
    snapshot_enabled = _snapshot(approval=True)
    live_disabled = _snapshot(approval=False)

    instance = SimpleNamespace(steps_config_snapshot=snapshot_enabled)
    defn = SimpleNamespace(steps_config=live_disabled)

    with (
        patch("app.services.workflow_engine.get_workflow_instance", AsyncMock(return_value=instance)),
        patch(
            "app.services.workflow_engine.get_or_create_workflow_definition",
            AsyncMock(return_value=defn),
        ),
    ):
        enabled = await is_step_enabled(
            db, uuid.uuid4(), "approval", invoice_id=uuid.uuid4()
        )
    assert enabled is True


@pytest.mark.asyncio
async def test_is_step_enabled_inverse_snapshot_wins_when_live_def_flipped_on():
    """Snapshot says approval is disabled; live def now says enabled.
    Runtime MUST stick to the snapshot — admins flipping the toggle
    cannot retroactively add an approval gate to invoices already
    mid-flight."""
    db = AsyncMock()
    snapshot_disabled = _snapshot(approval=False)
    live_enabled = _snapshot(approval=True)

    instance = SimpleNamespace(steps_config_snapshot=snapshot_disabled)
    defn = SimpleNamespace(steps_config=live_enabled)

    with (
        patch("app.services.workflow_engine.get_workflow_instance", AsyncMock(return_value=instance)),
        patch(
            "app.services.workflow_engine.get_or_create_workflow_definition",
            AsyncMock(return_value=defn),
        ),
    ):
        enabled = await is_step_enabled(
            db, uuid.uuid4(), "approval", invoice_id=uuid.uuid4()
        )
    assert enabled is False


@pytest.mark.asyncio
async def test_is_step_enabled_no_invoice_id_uses_live_definition():
    """When no invoice_id is supplied (e.g., during invoice creation,
    before any instance exists), runtime reads the *live* definition.
    This is the only path that picks up admin edits."""
    db = AsyncMock()
    live_enabled = _snapshot(extraction=True)
    defn = SimpleNamespace(steps_config=live_enabled)

    with patch(
        "app.services.workflow_engine.get_or_create_workflow_definition",
        AsyncMock(return_value=defn),
    ):
        enabled = await is_step_enabled(db, uuid.uuid4(), "extraction")
    assert enabled is True


@pytest.mark.asyncio
async def test_is_step_enabled_falls_back_when_instance_has_null_snapshot():
    """Legacy / backfill case: an instance exists but its
    steps_config_snapshot is None. Runtime must fall back to the live
    definition rather than crashing or returning the default."""
    db = AsyncMock()
    instance = SimpleNamespace(steps_config_snapshot=None)
    defn = SimpleNamespace(steps_config=_snapshot(approval=True))

    with (
        patch("app.services.workflow_engine.get_workflow_instance", AsyncMock(return_value=instance)),
        patch(
            "app.services.workflow_engine.get_or_create_workflow_definition",
            AsyncMock(return_value=defn),
        ),
    ):
        enabled = await is_step_enabled(
            db, uuid.uuid4(), "approval", invoice_id=uuid.uuid4()
        )
    assert enabled is True


@pytest.mark.asyncio
async def test_is_step_enabled_falls_back_when_instance_missing():
    """Pre-snapshot invoice (legacy data with no WorkflowInstance row
    at all) must not blow up — fall back to the live def."""
    db = AsyncMock()
    defn = SimpleNamespace(steps_config=_snapshot(erp_export=True))

    with (
        patch("app.services.workflow_engine.get_workflow_instance", AsyncMock(return_value=None)),
        patch(
            "app.services.workflow_engine.get_or_create_workflow_definition",
            AsyncMock(return_value=defn),
        ),
    ):
        enabled = await is_step_enabled(
            db, uuid.uuid4(), "erp_export", invoice_id=uuid.uuid4()
        )
    assert enabled is True


def test_check_step_enabled_defaults_to_enabled_for_unknown_type():
    """If a future step type isn't in the snapshot at all, treat it as
    enabled. The alternative (default-disabled) would silently break
    every existing invoice on a deployment that introduces a new step
    type without backfilling snapshots."""
    cfg = _snapshot()
    assert _check_step_enabled(cfg, "future_step_that_does_not_exist") is True


def test_check_step_enabled_default_steps_are_all_disabled():
    """DEFAULT_STEPS_CONFIG — the seed shape for a freshly-provisioned
    org — must have every step disabled. The "everything off by
    default" stance is the safe one: admins opt in to each gate."""
    for step in DEFAULT_STEPS_CONFIG["steps"]:
        assert step["enabled"] is False, (
            f"step {step['type']} is enabled in DEFAULT_STEPS_CONFIG — "
            "regression: defaults must be opt-in"
        )


def test_get_step_config_returns_nested_config_dict():
    """get_step_config is how every caller (extraction, review, etc.)
    reads per-step parameters like auto_approve_threshold or
    approver_id. Pin the lookup contract."""
    snapshot = {
        "steps": [
            {"type": "approval", "enabled": True, "config": {
                "approver_id": "user-123",
                "approver_strategy": "manual",
                "require_segregation": True,
            }},
        ]
    }
    cfg = get_step_config(snapshot, "approval")
    assert cfg["approver_id"] == "user-123"
    assert cfg["require_segregation"] is True


def test_get_step_config_missing_step_returns_empty_dict():
    """When the step type isn't in the snapshot, return {} so callers
    can `.get(...)` defaults without a None-check."""
    assert get_step_config(_snapshot(), "nonexistent") == {}


def test_get_step_config_step_without_config_returns_empty_dict():
    """A step entry that has no "config" key (legacy minimal entry)
    must still return {} from get_step_config — never raise."""
    snapshot = {"steps": [{"type": "approval", "enabled": True}]}  # no config key
    assert get_step_config(snapshot, "approval") == {}
