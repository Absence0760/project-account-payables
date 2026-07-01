"""Approval escalation sweeper.

When a chain level has been waiting longer than its configured
``escalation_hours`` without enough approvals to advance, this sweeper
appends the level's ``escalation_to_user_ids`` onto its ``approver_ids``
list — so those users become eligible to approve and unblock the chain.

The mutation lives in JSONB on `WorkflowInstance.state_data.approval_levels`
and is idempotent (no-op once a level has been escalated to a given user
set), so the sweeper is safe to run on a tight interval and across
overlapping replicas.

Pure async — runs as a long-lived asyncio task started in `main.lifespan`.
Cancelling the task (server shutdown) is handled cleanly. Also exposed as
a CLI: `python scripts/sweep_approval_escalations.py`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.organization import Organization
from app.models.workflow import WorkflowInstance
from app.services.approval_chain import apply_escalation
from app.services.audit_dispatch import dispatch_audit

logger = logging.getLogger(__name__)


def _last_escalation_detail(instance: WorkflowInstance) -> dict:
    """The escalation event apply_escalation just appended to the current level.

    Escalation expands who may approve an invoice — a material control event —
    so it must land in the immutable audit trail, not only in mutable
    state_data. Extracts the current level's most-recent escalation (the added
    approver ids + after_hours) for the audit ``details``; PII-free (only user
    ids + hours, never bank/tax data)."""
    chain = (instance.state_data or {}).get("approval_levels") or {}
    levels = chain.get("levels") or []
    idx = chain.get("current_level", 0)
    detail: dict = {"level": idx}
    if 0 <= idx < len(levels):
        escalations = levels[idx].get("escalations") or []
        if escalations:
            last = escalations[-1]
            detail["added_user_ids"] = last.get("added_user_ids")
            detail["after_hours"] = last.get("after_hours")
            detail["at"] = last.get("at")
    return detail


@dataclass
class EscalateResult:
    tenants_scanned: int = 0
    instances_escalated: int = 0
    failures: int = 0


async def escalate_once(*, now: datetime | None = None) -> EscalateResult:
    """One sweep across every tenant. Safe for direct CLI invocation."""
    now = now or datetime.now(UTC)
    result = EscalateResult()

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(select(Organization.id, Organization.db_name))
        tenants = list(rows.all())

    for org_id, db_name in tenants:
        result.tenants_scanned += 1
        try:
            n = await _escalate_tenant(db_name, now, org_id=org_id)
            result.instances_escalated += n
        except Exception as exc:
            # Log the exception CLASS only — a raw message could carry PII.
            logger.warning(
                "[approval-escalation] failed to sweep %s: %s", db_name, exc.__class__.__name__
            )
            result.failures += 1

    if result.instances_escalated or result.failures:
        logger.info(
            "[approval-escalation] swept %d tenant(s); escalated=%d failed_sweeps=%d",
            result.tenants_scanned,
            result.instances_escalated,
            result.failures,
        )
    return result


async def _escalate_tenant(db_name: str, now: datetime, *, org_id: uuid.UUID | None = None) -> int:
    """Mutate every active instance whose current chain level is overdue.

    Reads only `state="active"` rows — completed/abandoned instances don't
    move money and shouldn't bring a sweeper down if their JSON is malformed.

    The instance rows are locked ``FOR UPDATE`` for the sweep so an escalation
    can't clobber a concurrent approval: the approve path (review.approve_invoice)
    takes the same row lock, so escalation either waits for an in-flight approval
    to commit (then reads the fresh state_data) or holds the row while it
    escalates. Each escalation writes an ``invoice.approval_escalated`` audit row
    — expanding who may approve an invoice is a material control event and must
    be reconstructable from the immutable trail, not just mutable state_data.
    """
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    escalated = 0

    try:
        async with factory() as db:
            instances = (
                (
                    await db.execute(
                        select(WorkflowInstance)
                        .where(WorkflowInstance.state == "active")
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )

            for inst in instances:
                if apply_escalation(inst, now=now):
                    escalated += 1
                    if org_id is not None:
                        await dispatch_audit(
                            db,
                            correlation_id=inst.correlation_id or uuid.uuid4(),
                            organization_id=org_id,
                            actor_id=None,  # system-initiated sweep
                            action="invoice.approval_escalated",
                            entity_type="invoice",
                            entity_id=inst.invoice_id,
                            details=_last_escalation_detail(inst),
                        )

            if escalated:
                await db.commit()
                logger.info(
                    "[approval-escalation] %s: escalated %d instance(s)", db_name, escalated
                )
    finally:
        await engine.dispose()

    return escalated


async def run_escalation_loop() -> None:
    """Long-lived loop. Started in `main.lifespan` on app startup, cancelled
    on shutdown."""
    interval = settings.approval_escalation_interval_seconds
    logger.info("[approval-escalation] started; interval=%ds", interval)
    try:
        while True:
            try:
                await escalate_once()
            except Exception as exc:
                logger.error("[approval-escalation] sweep raised: %s", exc, exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[approval-escalation] shutting down")
        raise
