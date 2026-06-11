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
    def __init__(self, instances: list) -> None:
        self._instances = instances
        self.commit = AsyncMock()

    async def __aenter__(self) -> _FakeTenantSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def execute(self, *_a, **_k):
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=self._instances)
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars)
        return result


# ---------------------------------------------------------------------------
# escalate_once — multi-tenant fan-out
# ---------------------------------------------------------------------------


async def test_escalate_once_iterates_every_tenant():
    with (
        patch.object(
            approval_escalation,
            "control_session_factory",
            _fake_control_session(["ap_a", "ap_b", "ap_c"]),
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
            _fake_control_session(["ap_a", "ap_b", "ap_c"]),
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


async def test_escalate_tenant_commits_only_when_something_escalated():
    session = _FakeTenantSession([object(), object()])
    engine, patches = _patch_tenant(session)
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(approval_escalation, "apply_escalation", MagicMock(side_effect=[True, False])),
    ):
        n = await approval_escalation._escalate_tenant("ap_acme", datetime.now(UTC))

    assert n == 1
    session.commit.assert_awaited_once()
    engine.dispose.assert_awaited_once()


async def test_escalate_tenant_does_not_commit_when_nothing_overdue():
    session = _FakeTenantSession([object()])
    engine, patches = _patch_tenant(session)
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(approval_escalation, "apply_escalation", MagicMock(return_value=False)),
    ):
        n = await approval_escalation._escalate_tenant("ap_acme", datetime.now(UTC))

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
