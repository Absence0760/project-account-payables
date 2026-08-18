"""Tests for the approval-escalation sweeper.

The mutation primitive (``apply_escalation``) is covered with the approval
chain. This file covers the *sweeper* around it — the multi-tenant fan-out,
per-tenant failure isolation, commit gating, and the long-lived loop — which
had no direct coverage. Mirrors test_extraction_reaper.py.

DB-free: the control session and per-tenant sweep are mocked so we assert
orchestration without a live Postgres. The ``state == 'active'`` filter itself
is a query detail enforced in SQL; the mutation it feeds is covered by the
apply_escalation tests.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import approval_escalation


def _fake_control_session(tenant_db_names: list[str]):
    """Async-CM control session whose execute().all() yields (org_id, db_name)."""
    fake_rows = [(f"org-{n}", n) for n in tenant_db_names]
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(all=lambda: fake_rows))
    cm = AsyncMock()
    cm.__aenter__.return_value = fake_session
    cm.__aexit__.return_value = None
    return MagicMock(return_value=cm)


class _FakeTenantSession:
    """Models the sweep's TWO-PHASE shape.

    Phase 1 is an UNLOCKED `select(WorkflowInstance.id)` page; phase 2 re-reads
    each id with `get(..., with_for_update=True)`, mutates, and commits — one
    row locked at a time. (The sweep used to select every active instance
    `FOR UPDATE` in a single unbounded statement, which is what this fake used
    to model.)
    """

    def __init__(self, instances: list) -> None:
        self._by_id: dict = {}
        for inst in instances:
            iid = uuid.uuid4()
            inst.id = iid
            if not hasattr(inst, "state"):
                inst.state = "active"
            self._by_id[iid] = inst
        self._pages_served = 0
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self) -> _FakeTenantSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def execute(self, *_a, **_k):
        # First call returns the whole id page; any later page is empty.
        ids = list(self._by_id) if self._pages_served == 0 else []
        self._pages_served += 1
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=ids)
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars)
        return result

    async def get(self, _model, ident, **_kwargs):
        return self._by_id.get(ident)


# ---------------------------------------------------------------------------
# escalate_once — multi-tenant fan-out
# ---------------------------------------------------------------------------


async def test_escalate_once_iterates_every_tenant():
    with (
        patch.object(
            approval_escalation,
            "control_session_factory",
            _fake_control_session(["feoh_a", "feoh_b", "feoh_c"]),
        ),
        patch.object(approval_escalation, "_escalate_tenant", AsyncMock(return_value=2)) as sweep,
    ):
        result = await approval_escalation.escalate_once()

    assert result.tenants_scanned == 3
    assert result.instances_escalated == 6  # 3 tenants × 2
    assert result.failures == 0
    assert sweep.await_count == 3


async def test_escalate_once_continues_after_one_tenant_fails():
    """A malformed/unreachable tenant must not abort the whole sweep."""
    side_effects = [2, RuntimeError("bad json"), 1]
    with (
        patch.object(
            approval_escalation,
            "control_session_factory",
            _fake_control_session(["feoh_a", "feoh_b", "feoh_c"]),
        ),
        patch.object(approval_escalation, "_escalate_tenant", AsyncMock(side_effect=side_effects)),
    ):
        result = await approval_escalation.escalate_once()

    assert result.tenants_scanned == 3
    assert result.instances_escalated == 3  # 2 + (skipped) + 1
    assert result.failures == 1


# ---------------------------------------------------------------------------
# _escalate_tenant — commit gating + engine disposal
# ---------------------------------------------------------------------------


def _patch_tenant(session):
    engine = MagicMock(dispose=AsyncMock())
    return engine, (
        patch.object(approval_escalation, "_make_tenant_url", MagicMock(return_value="url")),
        patch.object(approval_escalation, "create_async_engine", MagicMock(return_value=engine)),
        patch.object(
            approval_escalation, "async_sessionmaker", MagicMock(return_value=lambda: session)
        ),
    )


def _stub_instance() -> SimpleNamespace:
    return SimpleNamespace(
        state="active",
        correlation_id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),
        state_data={},
    )


async def test_escalate_tenant_commits_only_when_something_escalated():
    session = _FakeTenantSession([_stub_instance(), _stub_instance()])
    engine, patches = _patch_tenant(session)
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(approval_escalation, "apply_escalation", MagicMock(side_effect=[True, False])),
    ):
        n = await approval_escalation._escalate_tenant("feoh_acme", datetime.now(UTC))

    assert n == 1
    session.commit.assert_awaited_once()
    engine.dispose.assert_awaited_once()


async def test_escalate_tenant_writes_audit_row_per_escalation():
    """Every escalation is a material control event (it expands who may approve
    an invoice), so it must write an append-only `invoice.approval_escalated`
    audit row — not only mutate state_data."""
    inst = SimpleNamespace(
        state="active",
        correlation_id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),
        state_data={
            "approval_levels": {
                "current_level": 0,
                "levels": [
                    {
                        "approver_ids": ["u1", "u2"],
                        "escalations": [
                            {
                                "at": "2026-07-01T00:00:00+00:00",
                                "added_user_ids": ["u2"],
                                "after_hours": 24,
                            },
                        ],
                    }
                ],
            }
        },
    )
    session = _FakeTenantSession([inst])
    engine, patches = _patch_tenant(session)
    audit_calls: list[dict] = []

    async def _capture_audit(_db, **kwargs):
        audit_calls.append(kwargs)

    org_id = uuid.uuid4()
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(approval_escalation, "apply_escalation", MagicMock(return_value=True)),
        patch.object(approval_escalation, "dispatch_audit", _capture_audit),
    ):
        n = await approval_escalation._escalate_tenant(
            "feoh_acme", datetime.now(UTC), org_id=org_id
        )

    assert n == 1
    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call["action"] == "invoice.approval_escalated"
    assert call["entity_type"] == "invoice"
    assert call["entity_id"] == inst.invoice_id
    assert call["organization_id"] == org_id
    assert call["actor_id"] is None  # system sweep
    assert call["details"]["added_user_ids"] == ["u2"]


async def test_escalate_tenant_does_not_commit_when_nothing_overdue():
    session = _FakeTenantSession([_stub_instance()])
    engine, patches = _patch_tenant(session)
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(approval_escalation, "apply_escalation", MagicMock(return_value=False)),
    ):
        n = await approval_escalation._escalate_tenant("feoh_acme", datetime.now(UTC))

    assert n == 0
    session.commit.assert_not_awaited()
    engine.dispose.assert_awaited_once()  # disposed even on the no-op path


# ---------------------------------------------------------------------------
# run_escalation_loop — lifecycle
# ---------------------------------------------------------------------------


async def test_run_escalation_loop_cancels_cleanly():
    with patch.object(
        approval_escalation, "escalate_once", AsyncMock(return_value=SimpleNamespace())
    ):
        task = asyncio.create_task(approval_escalation.run_escalation_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled() or task.done()


async def test_run_escalation_loop_survives_a_failed_sweep():
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return SimpleNamespace()

    with (
        patch.object(approval_escalation, "escalate_once", flaky),
        patch.object(approval_escalation.settings, "approval_escalation_interval_seconds", 0.01),
    ):
        task = asyncio.create_task(approval_escalation.run_escalation_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert call_count >= 2  # didn't die on the first raise


# A sentinel that stands in for a tenant-DB error's message fragment. It must
# never reach a log record (PII-out-of-logs invariant) — only the exception
# CLASS may.
_PII_SENTINEL = "SECRET_ACCOUNT_1234567890"


async def test_run_escalation_loop_failure_logs_exception_class_not_message(caplog):
    """The long-lived loop's top-level catch logs the exception CLASS only
    (with exc_info for the traceback), never the raw message."""

    async def flaky():
        raise RuntimeError(_PII_SENTINEL)

    with (
        patch.object(approval_escalation, "escalate_once", flaky),
        patch.object(approval_escalation.settings, "approval_escalation_interval_seconds", 0.01),
        caplog.at_level(logging.ERROR, logger=approval_escalation.logger.name),
    ):
        task = asyncio.create_task(approval_escalation.run_escalation_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "expected an ERROR log for the failed sweep"
    for record in errors:
        assert _PII_SENTINEL not in record.getMessage()
    assert any("RuntimeError" in r.getMessage() for r in errors)


# ---------------------------------------------------------------------------
# Real-Postgres: the state=='active' WHERE filter and the committed mutation —
# the gap between the well-tested apply_escalation primitive and the sweeper.
# ---------------------------------------------------------------------------


async def test_escalate_tenant_escalates_only_active_overdue_instances(realdb):
    from datetime import timedelta
    from decimal import Decimal

    from app.models.invoice import Invoice
    from app.models.workflow import WorkflowDefinition, WorkflowInstance
    from app.services.approval_escalation import _escalate_tenant

    info = realdb.info("a")
    org_id = info.org_id
    mk = realdb.sessionmaker("a")

    target_uid = str(uuid.uuid4())
    entered = (datetime.now(UTC) - timedelta(hours=48)).isoformat()

    def _overdue_state():
        return {
            "approval_levels": {
                "current_level": 0,
                "levels": [
                    {
                        "escalation_hours": 24,
                        "escalation_to_user_ids": [target_uid],
                        "entered_at": entered,
                        "approver_ids": [str(uuid.uuid4())],
                    }
                ],
            }
        }

    async with mk() as s:
        inv_a = Invoice(
            organization_id=org_id, invoice_number="INV-A", vendor_name="Acme", amount=Decimal("10")
        )
        inv_b = Invoice(
            organization_id=org_id, invoice_number="INV-B", vendor_name="Acme", amount=Decimal("10")
        )
        defn = WorkflowDefinition(organization_id=org_id, name="def", steps_config={"steps": []})
        s.add_all([inv_a, inv_b, defn])
        await s.flush()
        active = WorkflowInstance(
            definition_id=defn.id, invoice_id=inv_a.id, state="active", state_data=_overdue_state()
        )
        completed = WorkflowInstance(
            definition_id=defn.id,
            invoice_id=inv_b.id,
            state="completed",
            state_data=_overdue_state(),
        )
        s.add_all([active, completed])
        await s.commit()
        active_id, completed_id = active.id, completed.id

    # The sweeper escalates exactly one instance (the active one).
    escalated = await _escalate_tenant(info.db_name, datetime.now(UTC))
    assert escalated == 1

    async with mk() as s:
        a = await s.get(WorkflowInstance, active_id)
        c = await s.get(WorkflowInstance, completed_id)
    a_approvers = a.state_data["approval_levels"]["levels"][0]["approver_ids"]
    c_approvers = c.state_data["approval_levels"]["levels"][0]["approver_ids"]
    # Active: escalation target appended + committed.
    assert target_uid in a_approvers
    # Completed: untouched — the state=='active' filter excluded it.
    assert target_uid not in c_approvers


# ---------------------------------------------------------------------------
# Locking discipline — one row at a time, deterministic order (bug-hunt #9).
# ---------------------------------------------------------------------------


async def test_escalate_tenant_locks_one_row_at_a_time():
    """The sweep must never hold a lock on every active instance at once.

    `review.approve_invoice` takes the same row lock, so an unbounded
    `SELECT ... FOR UPDATE` over every active instance blocked the tenant's
    whole approval surface for the duration of the tick — and two replicas
    locking overlapping sets in unspecified order deadlocked. Phase 1 must
    therefore be unlocked, and phase 2 must lock exactly one row per `get`.
    """
    session = _FakeTenantSession([_stub_instance(), _stub_instance()])
    engine, patches = _patch_tenant(session)

    executed: list = []
    locked_gets: list = []
    real_execute = session.execute
    real_get = session.get

    async def _spy_execute(stmt=None, *a, **k):
        executed.append(stmt)
        return await real_execute(stmt, *a, **k)

    async def _spy_get(model, ident, **kwargs):
        locked_gets.append(kwargs.get("with_for_update"))
        return await real_get(model, ident, **kwargs)

    session.execute = _spy_execute
    session.get = _spy_get

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(approval_escalation, "apply_escalation", MagicMock(return_value=True)),
    ):
        n = await approval_escalation._escalate_tenant("feoh_acme", datetime.now(UTC))

    assert n == 2
    # Phase 1: the candidate page is a plain SELECT — no FOR UPDATE.
    page_sql = str(executed[0]).upper()
    assert "FOR UPDATE" not in page_sql
    assert "ORDER BY" in page_sql  # deterministic lock order across replicas
    assert "LIMIT" in page_sql
    # Phase 2: one locked read per candidate.
    assert locked_gets == [True, True]
    # And one commit each, so the lock is released before the next row.
    assert session.commit.await_count == 2


async def test_escalate_tenant_releases_the_lock_when_nothing_changes():
    """A locked row the sweep decides not to escalate must be released now, not
    held until the end of the tick."""
    session = _FakeTenantSession([_stub_instance()])
    engine, patches = _patch_tenant(session)

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(approval_escalation, "apply_escalation", MagicMock(return_value=False)),
    ):
        n = await approval_escalation._escalate_tenant("feoh_acme", datetime.now(UTC))

    assert n == 0
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


async def test_escalate_tenant_skips_an_instance_that_completed_under_the_lock():
    """Between the unlocked id read and the lock, an instance can be approved
    and completed. It must be skipped, not escalated."""
    stale = _stub_instance()
    stale.state = "completed"
    session = _FakeTenantSession([stale])
    engine, patches = _patch_tenant(session)

    apply_spy = MagicMock(return_value=True)
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(approval_escalation, "apply_escalation", apply_spy),
    ):
        n = await approval_escalation._escalate_tenant("feoh_acme", datetime.now(UTC))

    assert n == 0
    apply_spy.assert_not_called()
    session.rollback.assert_awaited_once()


async def test_escalate_tenant_pages_until_the_tenant_is_exhausted(realdb):
    """A tenant with more candidates than one page must still be fully swept.

    The page size is NOT a per-tick cap: escalation doesn't change `state`, so
    a capped sweep would re-read the same lowest-id rows every tick and never
    reach the rest.
    """
    from datetime import timedelta
    from decimal import Decimal

    from app.config import settings as cfg
    from app.models.invoice import Invoice
    from app.models.workflow import WorkflowDefinition, WorkflowInstance
    from app.services.approval_escalation import _escalate_tenant

    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    entered = (datetime.now(UTC) - timedelta(hours=48)).isoformat()

    def _overdue_state(target_uid: str) -> dict:
        return {
            "approval_levels": {
                "current_level": 0,
                "levels": [
                    {
                        "escalation_hours": 24,
                        "escalation_to_user_ids": [target_uid],
                        "entered_at": entered,
                        "approver_ids": [str(uuid.uuid4())],
                    }
                ],
            }
        }

    target_uid = str(uuid.uuid4())
    async with mk() as s:
        defn = WorkflowDefinition(
            organization_id=info.org_id, name="page-def", steps_config={"steps": []}
        )
        s.add(defn)
        await s.flush()
        for i in range(5):
            inv = Invoice(
                organization_id=info.org_id,
                invoice_number=f"INV-PAGE-{i}",
                vendor_name="Acme",
                amount=Decimal("10"),
            )
            s.add(inv)
            await s.flush()
            s.add(
                WorkflowInstance(
                    definition_id=defn.id,
                    invoice_id=inv.id,
                    state="active",
                    state_data=_overdue_state(target_uid),
                )
            )
        # An instance with NO approval chain — narrowed out in SQL, never locked.
        inv_bare = Invoice(
            organization_id=info.org_id,
            invoice_number="INV-PAGE-BARE",
            vendor_name="Acme",
            amount=Decimal("10"),
        )
        s.add(inv_bare)
        await s.flush()
        s.add(
            WorkflowInstance(
                definition_id=defn.id, invoice_id=inv_bare.id, state="active", state_data={}
            )
        )
        await s.commit()

    original = cfg.approval_escalation_batch_size
    cfg.approval_escalation_batch_size = 2
    try:
        escalated = await _escalate_tenant(info.db_name, datetime.now(UTC))
    finally:
        cfg.approval_escalation_batch_size = original

    assert escalated == 5, "every candidate must be reached across pages"
