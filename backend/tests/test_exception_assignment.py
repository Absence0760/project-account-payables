"""Tests for the exception-queue improvements:
  - SLA + auto-assignment baked in at creation (`_ensure_exception`)
  - `_apply_resolution` computes time_to_resolution_seconds in
    terminal states only
  - Response shape (`_exception_dict`) carries SLA + overdue flags

End-to-end coverage of the new endpoints (assign, bulk/resolve) lives
in the e2e suite — these tests pin the pure-Python edges so a logic
regression doesn't have to wait for the Playwright run.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# ---------- _ensure_exception: SLA + auto-assign --------------------------


def _invoice():
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),  # multi-entity P2: exception inherits invoice entity
        vendor_id=None,
    )


def _capture_db():
    """An AsyncMock session that captures `db.add(...)` calls so tests
    can inspect what got persisted."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))
    db.add = MagicMock()
    return db


def test_ensure_exception_writes_due_at_when_org_sla_set():
    from app.services.invoice_warnings import _ensure_exception

    inv = _invoice()
    db = _capture_db()
    org_settings = {"exceptions": {"default_sla_hours": 4}}

    asyncio.run(_ensure_exception(db, inv, "fraud_flag", "warning", "x", org_settings=org_settings))

    persisted = db.add.call_args.args[0]
    assert persisted.due_at is not None
    # Within ~5s of (now + 4h) — wall-clock tolerance.
    delta = persisted.due_at - datetime.now(UTC)
    assert timedelta(hours=3, minutes=59) < delta < timedelta(hours=4, minutes=1)


def test_ensure_exception_per_type_sla_overrides_default():
    from app.services.invoice_warnings import _ensure_exception

    inv = _invoice()
    db = _capture_db()
    org_settings = {
        "exceptions": {
            "default_sla_hours": 24,
            "sla_hours_by_type": {"fraud_flag": 2},
        }
    }
    asyncio.run(_ensure_exception(db, inv, "fraud_flag", "error", "x", org_settings=org_settings))

    persisted = db.add.call_args.args[0]
    delta = persisted.due_at - datetime.now(UTC)
    assert timedelta(hours=1, minutes=59) < delta < timedelta(hours=2, minutes=1)


def test_ensure_exception_no_sla_leaves_due_at_null():
    """Without org config, SLA is opt-in — leave due_at null so the UI
    doesn't render a meaningless 'overdue' badge."""
    from app.services.invoice_warnings import _ensure_exception

    inv = _invoice()
    db = _capture_db()
    asyncio.run(_ensure_exception(db, inv, "duplicate", "warning", "x"))

    persisted = db.add.call_args.args[0]
    assert persisted.due_at is None


def test_ensure_exception_auto_assigns_user_when_routing_set():
    from app.services.invoice_warnings import _ensure_exception

    inv = _invoice()
    db = _capture_db()
    target = uuid.uuid4()
    org_settings = {"exceptions": {"auto_assign_by_type": {"fraud_flag": str(target)}}}
    asyncio.run(_ensure_exception(db, inv, "fraud_flag", "warning", "x", org_settings=org_settings))

    persisted = db.add.call_args.args[0]
    assert persisted.assigned_to_user_id == target


def test_ensure_exception_skips_invalid_assignee_uuid():
    """Misconfigured org settings shouldn't break exception creation —
    log + leave assignee unset."""
    from app.services.invoice_warnings import _ensure_exception

    inv = _invoice()
    db = _capture_db()
    org_settings = {"exceptions": {"auto_assign_by_type": {"fraud_flag": "not-a-uuid"}}}

    asyncio.run(_ensure_exception(db, inv, "fraud_flag", "warning", "x", org_settings=org_settings))
    persisted = db.add.call_args.args[0]
    assert persisted.assigned_to_user_id is None


def test_ensure_exception_no_op_when_already_open():
    """The dedup check at the top of _ensure_exception should fire if
    an open exception of the same type already exists."""
    from app.services.invoice_warnings import _ensure_exception

    inv = _invoice()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=1)))
    db.add = MagicMock()

    asyncio.run(_ensure_exception(db, inv, "fraud_flag", "warning", "x"))
    db.add.assert_not_called()


# ---------- _apply_resolution: time-to-resolution computation -------------


def _exception(*, status="open", created_at=None):
    """Stand-in with the columns _apply_resolution touches."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        created_at=created_at or (datetime.now(UTC) - timedelta(hours=3)),
        resolution=None,
        resolved_by=None,
        resolved_at=None,
        time_to_resolution_seconds=None,
    )


def test_apply_resolution_resolved_writes_time_to_resolution():
    from app.api.exceptions import _apply_resolution

    exc = _exception(created_at=datetime.now(UTC) - timedelta(hours=2, minutes=30))
    _apply_resolution(exc, "resolve", "rule tuned", "Demo Admin")

    assert exc.status == "resolved"
    assert exc.resolved_by == "Demo Admin"
    assert exc.resolved_at is not None
    assert 8990 < exc.time_to_resolution_seconds < 9010  # ~2h30m


def test_apply_resolution_dismissed_writes_time_to_resolution():
    """Dismiss is a terminal state too — the SLA clock stops here."""
    from app.api.exceptions import _apply_resolution

    exc = _exception(created_at=datetime.now(UTC) - timedelta(hours=1))
    _apply_resolution(exc, "dismiss", "false positive", "Demo Admin")
    assert exc.status == "dismissed"
    assert exc.time_to_resolution_seconds is not None


def test_apply_resolution_escalate_does_not_close_sla_clock():
    """Escalation is intermediate — the original SLA still applies
    once a downstream resolver acts. Don't burn the
    time_to_resolution slot."""
    from app.api.exceptions import _apply_resolution

    exc = _exception(created_at=datetime.now(UTC) - timedelta(hours=1))
    _apply_resolution(exc, "escalate", "needs CFO", "Demo Manager")

    assert exc.status == "escalated"
    assert exc.time_to_resolution_seconds is None


# ---------- _exception_dict: SLA / overdue surface ------------------------


def test_exception_dict_marks_overdue_when_past_due_at():
    from app.api.exceptions import _exception_dict

    exc = SimpleNamespace(
        id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),
        exception_type="fraud_flag",
        severity="warning",
        description="x",
        status="open",
        resolution=None,
        resolved_by=None,
        resolved_at=None,
        assigned_to=None,
        assigned_to_user_id=None,
        due_at=datetime.now(UTC) - timedelta(hours=1),
        time_to_resolution_seconds=None,
        created_at=datetime.now(UTC) - timedelta(hours=8),
    )
    body = _exception_dict(exc, None)
    assert body["is_overdue"] is True
    assert body["time_to_resolution_hours"] is None


def test_exception_dict_does_not_mark_terminal_states_overdue():
    """A resolved-but-late exception isn't 'overdue' anymore — the
    work is done. Only open/escalated rows can be overdue."""
    from app.api.exceptions import _exception_dict

    exc = SimpleNamespace(
        id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),
        exception_type="fraud_flag",
        severity="warning",
        description="x",
        status="resolved",
        resolution="x",
        resolved_by="Demo",
        resolved_at=datetime.now(UTC),
        assigned_to=None,
        assigned_to_user_id=None,
        due_at=datetime.now(UTC) - timedelta(hours=1),  # was overdue
        time_to_resolution_seconds=72000,
        created_at=datetime.now(UTC) - timedelta(hours=20),
    )
    body = _exception_dict(exc, None)
    assert body["is_overdue"] is False
    # Surfaces in hours rounded to 2dp.
    assert body["time_to_resolution_hours"] == 20.0


def test_exception_dict_carries_assignee_uuid():
    from app.api.exceptions import _exception_dict

    target = uuid.uuid4()
    exc = SimpleNamespace(
        id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),
        exception_type="fraud_flag",
        severity="warning",
        description="x",
        status="open",
        resolution=None,
        resolved_by=None,
        resolved_at=None,
        assigned_to="Demo Manager",
        assigned_to_user_id=target,
        due_at=None,
        time_to_resolution_seconds=None,
        created_at=datetime.now(UTC),
    )
    body = _exception_dict(exc, None)
    assert body["assigned_to_user_id"] == str(target)
    assert body["assigned_to"] == "Demo Manager"
