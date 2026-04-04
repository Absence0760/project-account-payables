"""Invoice workflow state machine — validates transitions and orchestrates steps."""

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowStep
from app.services.audit import log_action

# ---------- valid status transitions ----------

VALID_TRANSITIONS: dict[InvoiceStatus, set[InvoiceStatus]] = {
    InvoiceStatus.new: {InvoiceStatus.pending},
    InvoiceStatus.pending: {InvoiceStatus.ready_for_review, InvoiceStatus.failed},
    InvoiceStatus.ready_for_review: {InvoiceStatus.approved, InvoiceStatus.rejected},
    InvoiceStatus.approved: {InvoiceStatus.sending_to_erp},
    InvoiceStatus.rejected: {InvoiceStatus.ready_for_review, InvoiceStatus.new},
    InvoiceStatus.sending_to_erp: {InvoiceStatus.sent_to_erp, InvoiceStatus.failed},
    InvoiceStatus.sent_to_erp: set(),  # terminal
    InvoiceStatus.failed: {InvoiceStatus.pending, InvoiceStatus.sending_to_erp},
}

# Map step index → step type
STEP_TYPES = ["upload", "review", "erp_push", "done"]

DEFAULT_STEPS_CONFIG = {
    "steps": [
        {"number": 1, "type": "upload", "name": "Upload & Extract"},
        {"number": 2, "type": "review", "name": "Human Review"},
        {"number": 3, "type": "erp_push", "name": "Send to ERP"},
        {"number": 4, "type": "done", "name": "Complete"},
    ]
}


def validate_transition(current: InvoiceStatus, target: InvoiceStatus) -> None:
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot transition from '{current.value}' to '{target.value}'",
        )


async def get_invoice_for_update(
    db: AsyncSession, invoice_id: uuid.UUID
) -> Invoice:
    """Fetch an invoice with a row-level lock to prevent concurrent transitions."""
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id).with_for_update()
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

    await log_action(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=actor_id,
        action=action_name,
        entity_type="invoice",
        entity_id=invoice.id,
        details={**(details or {}), "old_status": old_status, "new_status": target_status.value},
    )
    return invoice


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


async def create_workflow_instance(
    db: AsyncSession, invoice: Invoice
) -> WorkflowInstance:
    defn = await get_or_create_workflow_definition(db, invoice.organization_id)
    instance = WorkflowInstance(
        correlation_id=invoice.correlation_id,
        definition_id=defn.id,
        invoice_id=invoice.id,
        current_step=0,
        state="active",
    )
    db.add(instance)
    await db.flush()
    return instance


async def get_workflow_instance(
    db: AsyncSession, invoice_id: uuid.UUID
) -> WorkflowInstance | None:
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
    step_number = STEP_TYPES.index(step_type) + 1
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
        step.completed_at = datetime.now(timezone.utc)
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
    next_index = STEP_TYPES.index(next_step_type)
    instance.current_step = next_index
    new_step = await create_workflow_step(
        db, instance, next_step_type, assigned_to=assigned_to
    )
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
    done_step.completed_at = datetime.now(timezone.utc)
    instance.current_step = len(STEP_TYPES) - 1
    instance.state = "completed"
