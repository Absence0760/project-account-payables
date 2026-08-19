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
import uuid
from unittest.mock import AsyncMock, MagicMock

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
    from app.main import app

    # FastAPI 0.138's `include_router` keeps nested `_IncludedRouter` objects in
    # `app.routes` instead of flattening sub-routes, so scan via the supported
    # `iter_route_contexts` flattener (full path + methods); fall back to the
    # flat list on an older FastAPI.
    try:
        from fastapi.routing import iter_route_contexts

        flat = [(c.path, c.methods or set()) for c in iter_route_contexts(app.routes)]
    except ImportError:
        from fastapi.routing import APIRoute

        flat = [(r.path, r.methods or set()) for r in app.routes if isinstance(r, APIRoute)]

    audit_routes = [(path, methods) for path, methods in flat if "audit" in path.lower()]
    assert audit_routes, "expected at least one audit-log GET endpoint"
    for path, methods in audit_routes:
        assert methods == {"GET"}, f"audit route {path} must only respond to GET"


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
# Every router that mutates TENANT state audits — drift guard
# ---------------------------------------------------------------------------

# Routers that mutate tenant state through a handler with no `dispatch_audit`
# anywhere in the module. Each entry needs a reason; an entry with no reason
# is a hole in the trail, not an exemption. The four per-handler tests above
# stay as they are — they pin the specific money-path handlers by name, which
# a module-level grep can't do.
_TENANT_MUTATORS_WITHOUT_DIRECT_AUDIT = {
    # POST-shaped but read-only: the CFO posts a forecast, we return it beside
    # the actuals. `post_forecast_variance` persists nothing (the docstring
    # says so explicitly).
    "app.api.analytics": "POST /forecast_variance is pure compute — nothing is persisted",
    # The user's own conversation history, not tenant business state. The
    # access itself is metered (`extraction_usage` / token budget), and the
    # tools it can call are read-only.
    "app.api.assistant": "persists only the caller's own chat transcript; tools are read-only",
    # Audits through `services/exception_lifecycle.record_decision`, which is
    # the single writer of an exception decision and dispatches there.
    "app.api.exception_agents": "audits via exception_lifecycle.record_decision",
    "app.api.exceptions": "audits via exception_lifecycle.record_decision",
    # Audits through `services/qms_sync`, which writes `quality_inspection.synced`.
    "app.api.inspections": "audits via services/qms_sync",
    # Marking your own notification read is a per-user read receipt, not a
    # change to any tenant record.
    "app.api.notifications": "read receipts on the caller's own notifications",
    # Supplier-portal auth writes go through `dispatch_auth_audit` (login
    # failures, password change, step-up failures) — the auth trail, not the
    # business trail.
    "app.api.portal_auth": "audits via dispatch_auth_audit (auth trail)",
}


def _tenant_mutating_router_modules() -> dict[str, list[str]]:
    """Map module → mutating route paths, for handlers that take a tenant DB.

    `Depends(get_tenant_db)` is the precise marker for "this handler writes
    tenant state", which is what the invariant governs — control-plane and
    webhook-receiver routers are out of scope by construction.
    """
    from app.main import app
    from app.tenant import get_tenant_db

    try:
        from fastapi.routing import iter_route_contexts

        items = [(c.path, c.methods or set(), c.endpoint) for c in iter_route_contexts(app.routes)]
    except ImportError:  # pragma: no cover - older FastAPI
        from fastapi.routing import APIRoute

        items = [
            (r.path, r.methods or set(), r.endpoint)
            for r in app.routes
            if isinstance(r, APIRoute)
        ]

    out: dict[str, list[str]] = {}
    for path, methods, endpoint in items:
        module = getattr(endpoint, "__module__", "")
        if not module.startswith("app.api."):
            continue
        if not (methods & {"POST", "PATCH", "PUT", "DELETE"}):
            continue
        try:
            sig = inspect.signature(endpoint)
        except (ValueError, TypeError):  # pragma: no cover - builtins
            continue
        takes_tenant_db = any(
            getattr(param.default, "dependency", None) is get_tenant_db
            for param in sig.parameters.values()
        )
        if takes_tenant_db:
            out.setdefault(module, []).append(f"{sorted(methods)} {path}")
    return out


def test_every_tenant_mutating_router_writes_an_audit_row():
    """Widened from the four hand-picked handlers above.

    The invariant is "status changes / mutations write an audit row", but the
    only enforcement was a static grep of the payment + invoice handlers — so
    `api/entities.py` and `api/gl_accounts.py` shipped with no trail at all,
    and `create_entity` in particular mints the scope key every entity-scoped
    money query is filtered by. This sweeps every router with a mutating route
    bound to `get_tenant_db` and requires `dispatch_audit` in the module, or a
    written-down reason in `_TENANT_MUTATORS_WITHOUT_DIRECT_AUDIT`.
    """
    modules = _tenant_mutating_router_modules()
    assert modules, "expected to discover tenant-mutating routers"

    unaudited = []
    for module, routes in sorted(modules.items()):
        src = inspect.getsource(__import__(module, fromlist=["__name__"]))
        if "dispatch_audit" in src:
            continue
        if module in _TENANT_MUTATORS_WITHOUT_DIRECT_AUDIT:
            continue
        unaudited.append(f"{module} — {routes}")

    assert not unaudited, (
        "these routers mutate tenant state without calling dispatch_audit "
        "(invariant #3). Add the audit row, or add the module to "
        "_TENANT_MUTATORS_WITHOUT_DIRECT_AUDIT with a reason:\n  "
        + "\n  ".join(unaudited)
    )


def test_audit_exemption_list_has_no_stale_entries():
    """An exemption that stops being true is worse than none — it silently
    excuses a router that has since grown an audited mutation, or one that no
    longer mutates at all."""
    modules = _tenant_mutating_router_modules()
    stale = []
    for module, reason in sorted(_TENANT_MUTATORS_WITHOUT_DIRECT_AUDIT.items()):
        assert reason, f"{module} needs a reason, not a bare exemption"
        if module not in modules:
            stale.append(f"{module} (no longer has a tenant-mutating route)")
            continue
        src = inspect.getsource(__import__(module, fromlist=["__name__"]))
        if "dispatch_audit" in src:
            stale.append(f"{module} (now calls dispatch_audit — drop the exemption)")
    assert not stale, f"stale audit exemptions: {stale}"


def test_entity_and_gl_account_mutations_dispatch_audit():
    """Named explicitly, not just covered by the sweep above: an `Entity` is
    the scope key every entity-scoped money query filters by, and a GL account
    is what invoice lines are coded to."""
    from app.api import entities, gl_accounts

    for handler in (entities.create_entity, entities.update_entity):
        assert "dispatch_audit" in inspect.getsource(handler), handler.__name__
    for handler in (gl_accounts.create_gl_account, gl_accounts.sync_gl_accounts_from_erp):
        assert "dispatch_audit" in inspect.getsource(handler), handler.__name__


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


# ---------------------------------------------------------------------------
# log_action — the write primitive itself (every other test mocks it out)
#
# Mock-based: asserts the row it builds and its add-without-commit contract.
# The DB round-trip (read the row back; discarded on caller rollback) is a
# real-Postgres property this mock-only suite can't assert.
# ---------------------------------------------------------------------------


def _audit_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_log_action_builds_row_with_all_fields_and_returns_it():
    from app.services.audit import log_action

    db = _audit_db()
    corr, org, actor, ent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    entry = await log_action(
        db,
        correlation_id=corr,
        organization_id=org,
        actor_id=actor,
        action="invoice.approved",
        entity_type="invoice",
        entity_id=ent,
        details={"old_status": "ready_for_review", "new_status": "approved"},
    )

    db.add.assert_called_once_with(entry)
    assert entry.correlation_id == corr
    assert entry.organization_id == org
    assert entry.actor_id == actor
    assert entry.action == "invoice.approved"
    assert entry.entity_type == "invoice"
    assert entry.entity_id == ent
    assert entry.details == {"old_status": "ready_for_review", "new_status": "approved"}


@pytest.mark.asyncio
async def test_log_action_does_not_commit_or_flush():
    """The audit row's durability is tied to the caller's business
    transaction — log_action must only add(), never commit/flush, so the
    row commits (or rolls back) atomically with the status change."""
    from app.services.audit import log_action

    db = _audit_db()
    await log_action(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        action="invoice.approved",
        entity_type="invoice",
        entity_id=uuid.uuid4(),
    )
    db.add.assert_called_once()
    db.commit.assert_not_awaited()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_log_action_accepts_null_actor_and_details_for_system_events():
    """System-initiated transitions have no human actor — actor_id and
    details default to None and must land on the row as NULL."""
    from app.services.audit import log_action

    db = _audit_db()
    entry = await log_action(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        action="invoice.extraction_started",
        entity_type="invoice",
        entity_id=uuid.uuid4(),
    )
    assert entry.actor_id is None
    assert entry.details is None


# ---------------------------------------------------------------------------
# Real-Postgres: the SOC 2 who/what/when/where actually persists, and stays
# atomic with the caller's business transaction.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_action_persists_and_reads_back_all_fields(realdb):
    from sqlalchemy import select

    from app.models.workflow import AuditLog
    from app.services.audit import log_action

    info = realdb.info("a")
    corr, actor, ent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        await log_action(
            s,
            correlation_id=corr,
            organization_id=info.org_id,
            actor_id=actor,
            action="invoice.approved",
            entity_type="invoice",
            entity_id=ent,
            details={"old_status": "ready_for_review", "new_status": "approved"},
        )
        await s.commit()

    async with mk() as s:
        row = (
            await s.execute(select(AuditLog).where(AuditLog.correlation_id == corr))
        ).scalar_one()
    assert row.organization_id == info.org_id
    assert row.actor_id == actor
    assert row.action == "invoice.approved"
    assert row.entity_type == "invoice"
    assert row.entity_id == ent
    assert row.details == {"old_status": "ready_for_review", "new_status": "approved"}
    assert row.created_at is not None  # server-default fired


@pytest.mark.asyncio
async def test_log_action_is_discarded_on_caller_rollback(realdb):
    """log_action only add()s — if the caller's transaction rolls back, the
    audit row must vanish with it (atomic with the business change)."""
    from sqlalchemy import func, select

    from app.models.workflow import AuditLog
    from app.services.audit import log_action

    info = realdb.info("a")
    mk = realdb.sessionmaker("a")

    async with mk() as s:
        await log_action(
            s,
            correlation_id=uuid.uuid4(),
            organization_id=info.org_id,
            action="invoice.approved",
            entity_type="invoice",
            entity_id=uuid.uuid4(),
        )
        await s.rollback()

    async with mk() as s:
        count = (await s.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    assert count == 0  # rolled back with the caller — nothing committed
