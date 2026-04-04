"""Review service — approve, reject, and resubmit invoices."""

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceStatus
from app.models.exception import Exception as APException
from app.services.workflow_engine import (
    advance_workflow,
    complete_current_step,
    create_workflow_step,
    get_workflow_instance,
    transition_invoice,
)


async def approve_invoice(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID,
    actor_name: str,
    corrections: dict | None = None,
) -> Invoice:
    # Apply any field corrections
    if corrections:
        field_map = {"vendor": "vendor_name"}
        for field, value in corrections.items():
            if value is not None:
                attr = field_map.get(field, field)
                setattr(invoice, attr, value)

    invoice.approval_date = date.today()
    invoice.approved_by = actor_name

    await transition_invoice(
        db,
        invoice,
        InvoiceStatus.approved,
        actor_id=actor_id,
        action_name="invoice.approved",
        details={"corrections": corrections} if corrections else None,
    )

    instance = await get_workflow_instance(db, invoice.id)
    if instance:
        await advance_workflow(db, instance, "erp_push", action="approved")

    return invoice


async def reject_invoice(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID,
    reason: str,
) -> Invoice:
    await transition_invoice(
        db,
        invoice,
        InvoiceStatus.rejected,
        actor_id=actor_id,
        action_name="invoice.rejected",
        details={"reason": reason},
    )

    # Create an exception record
    db.add(APException(
        invoice_id=invoice.id,
        exception_type="review_rejected",
        description=reason,
        status="open",
        organization_id=invoice.organization_id,
    ))

    instance = await get_workflow_instance(db, invoice.id)
    if instance:
        await complete_current_step(db, instance, "rejected")
        # Track rejection count
        state_data = instance.state_data or {}
        state_data["rejection_count"] = state_data.get("rejection_count", 0) + 1
        instance.state_data = state_data

    return invoice


async def resubmit_invoice(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID,
) -> Invoice:
    await transition_invoice(
        db,
        invoice,
        InvoiceStatus.ready_for_review,
        actor_id=actor_id,
        action_name="invoice.resubmitted",
    )

    instance = await get_workflow_instance(db, invoice.id)
    if instance:
        # Create a new review step
        await create_workflow_step(db, instance, "review")
        instance.current_step = 1  # review step index

    return invoice


async def assign_reviewer(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID,
    reviewer_id: uuid.UUID,
) -> None:
    from app.services.audit import log_action

    instance = await get_workflow_instance(db, invoice.id)
    if not instance:
        return

    # Find the current review step and assign it
    from sqlalchemy import select
    from app.models.workflow import WorkflowStep

    result = await db.execute(
        select(WorkflowStep)
        .where(
            WorkflowStep.instance_id == instance.id,
            WorkflowStep.step_type == "review",
            WorkflowStep.completed_at.is_(None),
        )
        .order_by(WorkflowStep.created_at.desc())
        .limit(1)
    )
    step = result.scalar_one_or_none()
    if step:
        step.assigned_to = reviewer_id

    await log_action(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=actor_id,
        action="invoice.assigned_for_review",
        entity_type="invoice",
        entity_id=invoice.id,
        details={"reviewer_id": str(reviewer_id)},
    )
