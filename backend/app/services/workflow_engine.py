"""Invoice workflow state machine — validates transitions and orchestrates steps."""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowStep
from app.services.audit_dispatch import dispatch_audit

_log = logging.getLogger(__name__)

# ---------- valid status transitions ----------

VALID_TRANSITIONS: dict[InvoiceStatus, set[InvoiceStatus]] = {
    InvoiceStatus.new: {
        InvoiceStatus.pending,
        InvoiceStatus.ready_for_review,
        InvoiceStatus.approved,
        InvoiceStatus.done,
    },
    InvoiceStatus.pending: {
        InvoiceStatus.ready_for_review,
        InvoiceStatus.approved,
        InvoiceStatus.failed,
    },
    InvoiceStatus.ready_for_review: {InvoiceStatus.approved, InvoiceStatus.rejected},
    InvoiceStatus.approved: {
        InvoiceStatus.sending_to_erp,
        InvoiceStatus.payment_scheduled,
        InvoiceStatus.done,
    },
    InvoiceStatus.rejected: {InvoiceStatus.ready_for_review, InvoiceStatus.new},
    InvoiceStatus.sending_to_erp: {InvoiceStatus.sent_to_erp, InvoiceStatus.failed},
    InvoiceStatus.sent_to_erp: {InvoiceStatus.posted_in_erp, InvoiceStatus.done},
    InvoiceStatus.posted_in_erp: {
        InvoiceStatus.payment_scheduled,
        InvoiceStatus.done,
    },
    InvoiceStatus.payment_scheduled: {InvoiceStatus.paid, InvoiceStatus.approved},
    InvoiceStatus.paid: {InvoiceStatus.done, InvoiceStatus.approved},
    InvoiceStatus.done: set(),  # terminal
    InvoiceStatus.failed: {InvoiceStatus.pending, InvoiceStatus.sending_to_erp},
}

# Map step index → step type
STEP_TYPES = ["extraction", "approval", "erp_export", "done"]

# Backwards-compatible aliases for old step type names
_STEP_TYPE_ALIASES = {"upload": "extraction", "review": "approval", "erp_push": "erp_export"}

DEFAULT_STEPS_CONFIG = {
    "steps": [
        {
            "number": 1,
            "type": "extraction",
            "name": "Data Extraction",
            "enabled": False,
            "config": {
                "auto_approve_enabled": False,
                "auto_approve_threshold": 0.95,
            },
        },
        {
            "number": 2,
            "type": "approval",
            "name": "Manager Approval",
            "enabled": False,
            "config": {
                "required": False,
                "approver_id": None,
                "approver_strategy": "manual",
                "require_segregation": True,
            },
        },
        {
            "number": 3,
            "type": "erp_export",
            "name": "ERP Export",
            "enabled": False,
            "config": {
                "erp_system": "default",
                "export_format": "json",
                "endpoint_url": "",
            },
        },
    ],
}


def _check_step_enabled(steps_config: dict, step_type: str) -> bool:
    """Check if a step type is enabled in a steps_config dict."""
    for step in steps_config.get("steps", []):
        if step.get("type") == step_type:
            return step.get("enabled", True)
    return True  # enabled by default if not configured


def get_step_config(steps_config: dict, step_type: str) -> dict:
    """Return the config dict for a specific step type, or empty dict."""
    for step in steps_config.get("steps", []):
        if step.get("type") == step_type:
            return step.get("config", {})
    return {}


async def is_step_enabled(
    db: AsyncSession,
    organization_id: uuid.UUID,
    step_type: str,
    *,
    invoice_id: uuid.UUID | None = None,
) -> bool:
    """Check if a step type is enabled.

    If invoice_id is provided, reads from the instance's frozen snapshot.
    Otherwise reads from the org's active workflow definition.
    """
    if invoice_id:
        instance = await get_workflow_instance(db, invoice_id)
        if instance and instance.steps_config_snapshot:
            return _check_step_enabled(instance.steps_config_snapshot, step_type)

    defn = await get_or_create_workflow_definition(db, organization_id)
    return _check_step_enabled(defn.steps_config, step_type)


def validate_transition(current: InvoiceStatus, target: InvoiceStatus) -> None:
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot transition from '{current.value}' to '{target.value}'",
        )


async def get_invoice_for_update(db: AsyncSession, invoice_id: uuid.UUID) -> Invoice:
    """Fetch an invoice with a row-level lock to prevent concurrent transitions.

    Eager-loads `extraction_results` so callers that build an
    InvoiceResponse from the returned row don't trigger an
    async-illegal lazy load inside `_priors_summary`. Every
    /api/invoices/<id>/* endpoint that returns InvoiceResponse goes
    through here.
    """
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice_id)
        .with_for_update()
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


async def transition_invoice(
    db: AsyncSession,
    invoice: Invoice,
    target_status: InvoiceStatus,
    *,
    actor_id: uuid.UUID | None = None,
    action_name: str,
    details: dict | None = None,
) -> Invoice:
    """Validate and apply a status transition, writing an audit log entry."""
    validate_transition(invoice.status, target_status)
    old_status = invoice.status.value
    invoice.status = target_status

    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=actor_id,
        action=action_name,
        entity_type="invoice",
        entity_id=invoice.id,
        details={**(details or {}), "old_status": old_status, "new_status": target_status.value},
    )

    # Best-effort notification fan-out. Keyed off the *resulting* status so all
    # the paths that converge on a given status (e.g. payment webhook + ERP
    # webhook + ERP-sync all reaching `paid`) notify once, here, rather than at
    # every call site. Never allowed to break the transition — notify_event
    # swallows its own failures, and this outer guard is a final backstop so a
    # bug in recipient resolution can't abort a committed status change.
    try:
        await _maybe_notify_transition(
            db,
            invoice,
            target_status,
            actor_id=actor_id,
            details=details,
        )
    except Exception:  # noqa: BLE001
        _log.exception("notification hook failed for invoice transition to %s", target_status.value)
    return invoice


async def _maybe_notify_transition(
    db: AsyncSession,
    invoice: Invoice,
    target_status: InvoiceStatus,
    *,
    actor_id: uuid.UUID | None,
    details: dict | None,
) -> None:
    """Map a status transition to a notification event + recipients and dispatch.

    The "assigned" event is NOT handled here (assignment doesn't always change
    status) — it's fired explicitly from `review.assign_reviewer`.
    """
    from app.models.notification import (
        EVENT_INVOICE_APPROVED,
        EVENT_INVOICE_PAID,
        EVENT_INVOICE_REJECTED,
    )
    from app.services.notification_dispatch import (
        notify_event,
        resolve_role_user_ids,
    )
    from app.services.notification_templates import InvoiceContext

    event_type: str | None = None
    recipients: list[uuid.UUID] = []

    # Read every invoice field defensively — the hook must never assume more
    # about `invoice` than `dispatch_audit` does (which only needs id /
    # correlation_id / organization_id). A missing optional field degrades to
    # "no notification," never an exception that aborts the transition.
    uploaded_by_id = getattr(invoice, "uploaded_by_id", None)

    if target_status is InvoiceStatus.approved:
        event_type = EVENT_INVOICE_APPROVED
        if uploaded_by_id:
            recipients.append(uploaded_by_id)
    elif target_status is InvoiceStatus.rejected:
        event_type = EVENT_INVOICE_REJECTED
        if uploaded_by_id:
            recipients.append(uploaded_by_id)
    elif target_status is InvoiceStatus.paid:
        event_type = EVENT_INVOICE_PAID
        if uploaded_by_id:
            recipients.append(uploaded_by_id)
        try:
            recipients.extend(await resolve_role_user_ids(invoice.organization_id, "ap_manager"))
        except Exception:  # noqa: BLE001 — role lookup must not break the transition
            pass

    if not event_type or not recipients:
        return

    ctx = InvoiceContext(
        invoice_number=getattr(invoice, "invoice_number", ""),
        vendor_name=getattr(invoice, "vendor_name", ""),
        amount=getattr(invoice, "amount", None),
        currency=getattr(invoice, "currency", None) or "USD",
        reason=(details or {}).get("reason"),
    )
    await notify_event(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        event_type=event_type,
        entity_id=invoice.id,
        recipient_user_ids=recipients,
        invoice_ctx=ctx,
        actor_id=actor_id,
    )


# ---------- workflow instance / step helpers ----------


async def get_or_create_workflow_definition(
    db: AsyncSession, organization_id: uuid.UUID
) -> WorkflowDefinition:
    result = await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.organization_id == organization_id,
            WorkflowDefinition.is_active == True,  # noqa: E712
        )
    )
    defn = result.scalar_one_or_none()
    if defn:
        return defn

    defn = WorkflowDefinition(
        name="Invoice Processing",
        description="Upload → Review → ERP → Done",
        steps_config=DEFAULT_STEPS_CONFIG,
        is_active=True,
        organization_id=organization_id,
    )
    db.add(defn)
    await db.flush()
    return defn


async def create_workflow_instance(db: AsyncSession, invoice: Invoice) -> WorkflowInstance:
    defn = await get_or_create_workflow_definition(db, invoice.organization_id)
    instance = WorkflowInstance(
        correlation_id=invoice.correlation_id,
        definition_id=defn.id,
        invoice_id=invoice.id,
        current_step=0,
        state="active",
        steps_config_snapshot=defn.steps_config,
    )
    db.add(instance)
    await db.flush()
    return instance


async def get_workflow_instance(db: AsyncSession, invoice_id: uuid.UUID) -> WorkflowInstance | None:
    result = await db.execute(
        select(WorkflowInstance).where(WorkflowInstance.invoice_id == invoice_id)
    )
    return result.scalar_one_or_none()


async def create_workflow_step(
    db: AsyncSession,
    instance: WorkflowInstance,
    step_type: str,
    *,
    assigned_to: uuid.UUID | None = None,
) -> WorkflowStep:
    resolved = _STEP_TYPE_ALIASES.get(step_type, step_type)
    step_number = STEP_TYPES.index(resolved) + 1
    step = WorkflowStep(
        correlation_id=instance.correlation_id,
        instance_id=instance.id,
        step_number=step_number,
        step_type=step_type,
        assigned_to=assigned_to,
    )
    db.add(step)
    await db.flush()
    return step


async def complete_current_step(
    db: AsyncSession,
    instance: WorkflowInstance,
    action: str,
) -> WorkflowStep | None:
    """Mark the most recent incomplete step as completed."""
    result = await db.execute(
        select(WorkflowStep)
        .where(
            WorkflowStep.instance_id == instance.id,
            WorkflowStep.completed_at.is_(None),
        )
        .order_by(WorkflowStep.step_number.desc())
        .limit(1)
    )
    step = result.scalar_one_or_none()
    if step:
        step.action = action
        step.completed_at = datetime.now(UTC)
    return step


async def advance_workflow(
    db: AsyncSession,
    instance: WorkflowInstance,
    next_step_type: str,
    *,
    action: str,
    assigned_to: uuid.UUID | None = None,
) -> WorkflowStep:
    """Complete the current step and create the next one."""
    await complete_current_step(db, instance, action)
    resolved_next = _STEP_TYPE_ALIASES.get(next_step_type, next_step_type)
    next_index = STEP_TYPES.index(resolved_next)
    instance.current_step = next_index
    new_step = await create_workflow_step(db, instance, next_step_type, assigned_to=assigned_to)
    return new_step


async def complete_workflow(
    db: AsyncSession,
    instance: WorkflowInstance,
    action: str = "completed",
) -> None:
    """Mark the workflow as completed."""
    await complete_current_step(db, instance, action)
    # Create the final "done" step
    done_step = await create_workflow_step(db, instance, "done")
    done_step.action = "completed"
    done_step.completed_at = datetime.now(UTC)
    instance.current_step = len(STEP_TYPES) - 1
    instance.state = "completed"
