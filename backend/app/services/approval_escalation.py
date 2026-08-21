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
from app.services.sweep_health import SWEEP_APPROVAL_ESCALATION, run_sweep_loop

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
    #: Individual workflow instances whose escalation raised. Counted apart from
    #: ``failures`` because one bad instance no longer takes its tenant's
    #: remaining pages down with it — mirrors
    #: ``vendor_rescreen.vendor_failures``. The ``*_failures`` suffix is
    #: load-bearing: ``sweep_health.failure_count`` sums it, so a sweep that
    #: keeps completing while instances inside it fail reports ``partial``.
    instance_failures: int = 0


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
            n, instance_failures = await _escalate_tenant(db_name, now, org_id=org_id)
            result.instances_escalated += n
            result.instance_failures += instance_failures
        except Exception as exc:
            # Log the exception CLASS only — a raw message could carry PII.
            logger.warning(
                "[approval-escalation] failed to sweep %s: %s", db_name, exc.__class__.__name__
            )
            result.failures += 1

    if result.instances_escalated or result.failures or result.instance_failures:
        logger.info(
            "[approval-escalation] swept %d tenant(s); escalated=%d "
            "failed_sweeps=%d failed_instances=%d",
            result.tenants_scanned,
            result.instances_escalated,
            result.failures,
            result.instance_failures,
        )
    return result


async def _escalate_tenant(
    db_name: str, now: datetime, *, org_id: uuid.UUID | None = None
) -> tuple[int, int]:
    """Mutate every active instance whose current chain level is overdue.

    Returns ``(escalated, failed_instances)``.

    Reads only `state="active"` rows carrying an ``approval_levels`` chain —
    completed/abandoned instances don't move money, and one with no chain can
    never escalate, so neither is worth locking.

    **Two-phase, one row locked at a time.** Candidate ids are selected
    UNLOCKED, keyset-paginated by id (``approval_escalation_batch_size`` per
    page); each id is then re-read with ``FOR UPDATE``, escalated, and committed
    on its own, which releases the lock before the next row is touched. The
    sweep used to select every active instance ``FOR UPDATE`` in one unbounded
    statement and hold all of it to the end of the tick: ``review.approve_invoice``
    takes the same row lock, so a tenant with a large open queue had its ENTIRE
    approval surface blocked behind each tick, and two replicas locking
    overlapping sets in unspecified order deadlocked and aborted the tick.
    Ordering by id gives every replica the same lock order, so they queue
    instead of deadlocking.

    Paginating rather than capping is deliberate: escalation does not change
    ``state``, so a capped sweep would re-examine the same lowest-id rows every
    tick and never reach the rest. The row lock still serialises against a
    concurrent approval — escalation either waits for it to commit (then reads
    the fresh ``state_data``) or holds the one row while it escalates.

    Each escalation writes an ``invoice.approval_escalated`` audit row —
    expanding who may approve an invoice is a material control event and must be
    reconstructable from the immutable trail, not just mutable state_data.
    """
    page_size = int(settings.approval_escalation_batch_size)
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    escalated = 0
    failed_instances = 0

    try:
        async with factory() as db:
            after: uuid.UUID | None = None
            while True:
                query = (
                    select(WorkflowInstance.id)
                    .where(
                        WorkflowInstance.state == "active",
                        # No chain, nothing to escalate — skip it in SQL rather
                        # than paying a lock + a JSON parse to learn that.
                        WorkflowInstance.state_data["approval_levels"].astext.isnot(None),
                    )
                    .order_by(WorkflowInstance.id.asc())
                    .limit(page_size)
                )
                if after is not None:
                    query = query.where(WorkflowInstance.id > after)
                instance_ids = (await db.execute(query)).scalars().all()
                if not instance_ids:
                    break
                after = instance_ids[-1]

                for instance_id in instance_ids:
                    # The per-instance `try` is what makes the keyset pagination
                    # above mean anything. `after` is a LOCAL that resets to
                    # `None` every tick, so a raise here used to unwind the whole
                    # `while` and the next tick restarted at page 1 — hitting the
                    # same instance (a malformed free-form `approval_levels`
                    # blob, an audit write that will not land) at the same place,
                    # forever. Nothing past it in that tenant was ever escalated:
                    # exactly the tail starvation this sweep was rewritten to
                    # page around, arriving through error handling instead of
                    # through a cap.
                    try:
                        # `with_for_update` bypasses the identity map, so this is
                        # a real `SELECT ... FOR UPDATE` on exactly one row.
                        inst = await db.get(WorkflowInstance, instance_id, with_for_update=True)
                        if inst is None or inst.state != "active":
                            # Deleted or completed between the id read and the lock.
                            await db.rollback()
                            continue
                        if not apply_escalation(inst, now=now):
                            # Nothing to write — end the transaction so the row
                            # lock is released immediately instead of at end of tick.
                            await db.rollback()
                            continue
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
                        await db.commit()
                    except Exception as exc:  # noqa: BLE001 — one instance must not halt the tenant
                        # Class only, never the message — an asyncpg / audit
                        # error string can echo row values (PII-out-of-logs).
                        logger.warning(
                            "[approval-escalation] instance=%s escalation failed in %s: %s",
                            instance_id,
                            db_name,
                            exc.__class__.__name__,
                        )
                        await db.rollback()
                        failed_instances += 1
                        continue
                    escalated += 1

                if len(instance_ids) < page_size:
                    break

            if escalated or failed_instances:
                logger.info(
                    "[approval-escalation] %s: escalated %d instance(s), %d failed",
                    db_name,
                    escalated,
                    failed_instances,
                )
    finally:
        await engine.dispose()

    return escalated, failed_instances


async def run_escalation_loop() -> None:
    """Long-lived loop. Started in `main.lifespan` on app startup, cancelled
    on shutdown. Body is the shared `sweep_health.run_sweep_loop`, so each
    tick's outcome (including `EscalateResult.failures`) is recorded."""
    await run_sweep_loop(
        SWEEP_APPROVAL_ESCALATION,
        lambda: escalate_once(),
        interval_seconds=settings.approval_escalation_interval_seconds,
        log=logger,
        log_prefix="[approval-escalation]",
    )
