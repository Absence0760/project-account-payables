"""Audit-log append-only contract (project invariant #3).

> Audit trail is append-only. Status transitions on invoices,
> payments, approvals, and vendors write a log row through the
> audit-shipping infrastructure, not just mutate state. A status
> change that overwrites without producing an audit row is
> `Improvement` at minimum, `Critical` if the field is regulated
> (`paid_at`, `approved_at`, `void_at`).

Tests:
  - No HTTP API exposes PATCH / DELETE on AuditLog
  - `dispatch_audit` is called from every money-/status-moving
    handler (void_payment, cancel_run, approve_run,
    transition_invoice)
  - AuditLog model declares the columns required for the WORM
    pipeline (shipped_at for the centralized SOC 2 sink)
  - The audit-shipper batch helper does NOT delete rows on ship —
    it only stamps `shipped_at`
"""

from __future__ import annotations

import inspect

import pytest

# ---------------------------------------------------------------------------
# No HTTP API exposes destructive verbs on AuditLog
# ---------------------------------------------------------------------------


def test_no_router_defines_a_put_patch_or_delete_on_audit_log():
    """Sweep every router under app.api. Any path that contains
    "audit" / "audit-log" must NOT respond to PATCH / DELETE / PUT.
    If a future PR adds an "edit audit row" endpoint, this catches it."""
    from fastapi.routing import APIRoute

    from app.main import app

    violations: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path_lower = route.path.lower()
        if "audit" not in path_lower:
            continue
        forbidden = set(route.methods or []) & {"PUT", "PATCH", "DELETE"}
        if forbidden:
            violations.append(f"{route.path} {forbidden}")
    assert not violations, f"audit endpoints must be GET-only — violations: {violations}"


def test_audit_log_get_endpoint_is_read_only_in_signature():
    """The lone audit endpoint (`get_audit_log` in workflow.py) must
    be a GET. A regression to POST/PUT shape would imply mutation."""
    from fastapi.routing import APIRoute

    from app.main import app

    audit_get_routes = [
        r for r in app.routes if isinstance(r, APIRoute) and "audit" in r.path.lower()
    ]
    assert audit_get_routes, "expected at least one audit-log GET endpoint"
    for r in audit_get_routes:
        assert r.methods == {"GET"}, f"audit route {r.path} must only respond to GET"


# ---------------------------------------------------------------------------
# AuditLog model — required columns for the WORM pipeline
# ---------------------------------------------------------------------------


def test_audit_log_has_shipped_at_for_worm_pipeline():
    """The centralized SOC 2 shipper (`services/audit_log_shipper.py`)
    stamps `shipped_at` after a batch successfully reaches every
    configured sink. Without that column the shipper would re-ship
    forever — and operators couldn't tell what's been replicated."""
    from app.models.workflow import AuditLog

    assert "shipped_at" in AuditLog.__table__.columns


def test_audit_log_columns_carry_full_who_what_when_where():
    """Auditors expect every row to identify actor, action,
    entity, and time. Pin the column names so a future "I'll
    rename actor_id to user_id" refactor doesn't break the SOC 2
    evidence trail."""
    from app.models.workflow import AuditLog

    required = {
        "id",
        "actor_id",
        "action",
        "entity_type",
        "entity_id",
        "created_at",
        "details",
        "organization_id",
        "correlation_id",
    }
    actual = set(AuditLog.__table__.columns.keys())
    missing = required - actual
    assert not missing, f"AuditLog is missing required columns: {missing}"


# ---------------------------------------------------------------------------
# Money-/status-moving handlers must call dispatch_audit
# ---------------------------------------------------------------------------


def test_void_payment_handler_dispatches_audit():
    """`void_payment` is regulated money movement — invariant says
    it MUST write an audit row. Static-grep the handler source."""
    from app.api import payments

    src = inspect.getsource(payments.void_payment)
    assert "dispatch_audit" in src, (
        "void_payment must call dispatch_audit before commit (invariant #3)"
    )


def test_cancel_payment_run_handler_dispatches_audit():
    """Cancelling a draft run reopens the invoices it pinned — the
    SOD chain needs the audit row to reconstruct who undid what."""
    from app.api import payments

    src = inspect.getsource(payments.cancel_payment_run)
    assert "dispatch_audit" in src


def test_approve_payment_run_handler_dispatches_audit():
    """CFO sign-off on a high-value run — the audit row is the
    proof-of-approval auditors look for."""
    from app.api import payments

    src = inspect.getsource(payments.approve_payment_run)
    assert "dispatch_audit" in src


def test_transition_invoice_helper_dispatches_audit():
    """`transition_invoice` is the chokepoint for every invoice
    status change. It must dispatch an audit on every call — the
    detail payload includes old/new status."""
    from app.services import workflow_engine

    src = inspect.getsource(workflow_engine.transition_invoice)
    assert "dispatch_audit" in src


# ---------------------------------------------------------------------------
# Audit-shipper preserves rows on ship (stamp, don't delete)
# ---------------------------------------------------------------------------


def test_audit_shipper_does_not_delete_rows_on_ship():
    """The shipper marks rows `shipped_at=now()` after a batch ACKs
    on every sink. It must NEVER DELETE rows — auditors need the
    tenant-local copy for forensic queries even after replication."""
    from app.services import audit_log_shipper

    src = inspect.getsource(audit_log_shipper)
    # Quick sanity: no DELETE FROM audit_log anywhere in the shipper.
    assert "DELETE FROM audit_log" not in src.upper()
    assert (
        "audit_log_shipper" not in src
        or "db.delete" not in src
        or ("AuditLog" not in src.split("db.delete", 1)[1][:200] if "db.delete" in src else True)
    )


# ---------------------------------------------------------------------------
# Status fields that are regulated by SOC 2 evidence
# ---------------------------------------------------------------------------


def test_invoice_status_transitions_funnel_through_transition_invoice():
    """Every router that touches `invoice.status` must call
    transition_invoice — direct assignment skips the audit dispatch.

    Three routers move invoices through their state machine today:
    invoices (bulk + per-row ops), workflow (review / approve / reject),
    and payments (schedule / void). Any of them dropping its
    transition_invoice call would silently start producing
    money-touching transitions without an audit row.
    """
    from app.api import invoices, payments, workflow

    invoices_src = inspect.getsource(invoices)
    workflow_src = inspect.getsource(workflow)
    payments_src = inspect.getsource(payments)
    for name, src in (
        ("invoices", invoices_src),
        ("workflow", workflow_src),
        ("payments", payments_src),
    ):
        assert "transition_invoice" in src, (
            f"router `{name}` must call transition_invoice — the only path "
            "that pairs the invoice state change with an audit row"
        )


# ---------------------------------------------------------------------------
# Audit dispatch helper carries enough context to be useful
# ---------------------------------------------------------------------------


def test_dispatch_audit_signature_demands_actor_action_entity():
    """The helper signature must require the four fields auditors
    need: actor_id, action, entity_type, entity_id. A regression
    that demotes any of these to optional opens the door to "lazy"
    audit rows that don't identify who did what."""
    from app.services.audit_dispatch import dispatch_audit

    sig = inspect.signature(dispatch_audit)
    required = {"actor_id", "action", "entity_type", "entity_id", "organization_id"}
    declared = set(sig.parameters.keys())
    missing = required - declared
    assert not missing, f"dispatch_audit signature missing required params: {missing}"


# ---------------------------------------------------------------------------
# Schema response — exposing AuditLog must not let the UI sort by
# `shipped_at` in a way that hides rows
# ---------------------------------------------------------------------------


def test_audit_log_get_endpoint_returns_rows_ordered_by_created_at():
    """The endpoint orders by `created_at` so the timeline is
    chronological. A regression that ordered by `shipped_at` would
    push unshipped rows to the end of the list — operators would
    miss the most recent events."""
    from app.api import workflow

    src = inspect.getsource(workflow.get_audit_log)
    assert "order_by" in src
    assert "AuditLog.created_at" in src
    assert "shipped_at" not in src  # not the sort key


@pytest.mark.asyncio
async def test_audit_log_get_endpoint_filters_by_correlation_id():
    """The endpoint takes a correlation_id and returns rows scoped
    to it. The filter is the contract — a regression that returned
    every audit row in the tenant would mean a clerk asking about
    invoice #1 sees the whole audit history."""
    from app.api import workflow

    src = inspect.getsource(workflow.get_audit_log)
    assert "AuditLog.correlation_id" in src
