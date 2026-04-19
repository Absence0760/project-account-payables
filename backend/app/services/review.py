"""Review service — approve, reject, and resubmit invoices."""

import logging
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.services.rag import store_embedding
from app.services.vendor_priors import record_corrections
from app.services.workflow_engine import (
    advance_workflow,
    complete_current_step,
    create_workflow_step,
    get_workflow_instance,
    transition_invoice,
)

_log = logging.getLogger(__name__)


async def _fetch_invoice_bytes(invoice: Invoice) -> bytes | None:
    """Best-effort S3 fetch of the invoice file for RAG embedding.

    Returns None on any failure — RAG storage is a learning-side-effect, not
    a correctness requirement, so we never block the approval on it.
    """
    file_key = invoice.file_key
    if not file_key:
        return None
    try:
        import boto3

        s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )
        obj = s3.get_object(Bucket=settings.s3_bucket, Key=file_key)
        return obj["Body"].read()
    except Exception as exc:
        _log.warning("Failed to fetch bytes for RAG embedding of %s: %s", invoice.id, exc)
        return None


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

        # Store vendor-consistent corrections in the correction cache so
        # future extractions from the same vendor pick up the right values.
        await record_corrections(db, invoice, corrections)

    # Upsert the RAG embedding using the invoice's NOW-correct fields.
    # Best-effort: failures (S3 unavailable, no text layer, embedding API
    # down) log and move on — the approval itself still commits.
    try:
        file_bytes = await _fetch_invoice_bytes(invoice)
        if file_bytes:
            await store_embedding(db, invoice, file_bytes=file_bytes)
    except Exception as exc:  # noqa: BLE001
        _log.warning("RAG embedding storage failed for %s: %s", invoice.id, exc)

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
    actor_name: str,
    reason: str,
) -> Invoice:
    invoice.rejected_by = actor_name
    await transition_invoice(
        db,
        invoice,
        InvoiceStatus.rejected,
        actor_id=actor_id,
        action_name="invoice.rejected",
        details={"reason": reason},
    )

    # Create an exception record
    db.add(
        APException(
            invoice_id=invoice.id,
            exception_type="review_rejected",
            description=reason,
            status="open",
            organization_id=invoice.organization_id,
        )
    )

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
    reviewer_name: str,
) -> None:
    invoice.assigned_to_id = reviewer_id
    invoice.assigned_to = reviewer_name
    from app.services.audit_dispatch import dispatch_audit

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

    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=actor_id,
        action="invoice.assigned_for_review",
        entity_type="invoice",
        entity_id=invoice.id,
        details={"reviewer_id": str(reviewer_id)},
    )
