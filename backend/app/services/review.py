"""Review service — approve, reject, and resubmit invoices."""

import logging
import uuid
from datetime import date

from sqlalchemy import select
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
    get_step_config,
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


async def _enforce_approval_thresholds(
    db: AsyncSession,
    invoice: Invoice,
    actor_roles: set[str],
) -> None:
    """Check approval thresholds from the workflow snapshot. Raises on violation."""
    from fastapi import HTTPException, status

    instance = await get_workflow_instance(db, invoice.id)
    if not instance or not instance.steps_config_snapshot:
        return

    config = get_step_config(instance.steps_config_snapshot, "approval")
    if not config:
        return

    amount = float(invoice.amount or 0)

    # Hard reject if over max
    max_amount = config.get("max_invoice_amount")
    if max_amount is not None and amount > max_amount:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(f"Invoice amount ${amount:,.2f} exceeds maximum allowed ${max_amount:,.2f}."),
        )

    # CFO role gate for high-value invoices
    cfo_threshold = config.get("require_cfo_above")
    if cfo_threshold is not None and amount > cfo_threshold:
        if "cfo" not in actor_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Invoice amount ${amount:,.2f} exceeds"
                    f" ${cfo_threshold:,.2f}. CFO approval required."
                ),
            )


async def approve_invoice(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID,
    actor_name: str,
    actor_roles: set[str] | None = None,
    corrections: dict | None = None,
) -> Invoice:
    from app.services.approval_chain import (
        advance_approval_chain,
        check_segregation,
    )

    # Read approval config from workflow snapshot
    instance = await get_workflow_instance(db, invoice.id)
    approval_config: dict = {}
    if instance and instance.steps_config_snapshot:
        approval_config = get_step_config(instance.steps_config_snapshot, "approval")

    # Segregation of duties: uploader cannot approve
    check_segregation(invoice, actor_id, approval_config)

    # Threshold enforcement
    await _enforce_approval_thresholds(db, invoice, actor_roles or set())

    # Apply any field corrections, capturing a per-field before/after diff for
    # the audit trail (SOX change-history requirement). Money fields serialise
    # as string-Decimal inside the diff (build_field_diff handles the typing).
    field_diff: dict = {}
    if corrections:
        from app.services.audit_access import build_field_diff

        field_map = {"vendor": "vendor_name"}
        before: dict = {}
        after: dict = {}
        for field, value in corrections.items():
            if value is not None:
                attr = field_map.get(field, field)
                before[attr] = getattr(invoice, attr, None)
                setattr(invoice, attr, value)
                after[attr] = getattr(invoice, attr, None)
        field_diff = build_field_diff(before, after, list(after.keys()))

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

    # Multi-level chain: check if this approval satisfies the current level
    # or if more levels remain.
    if approval_config.get("approver_strategy") == "chain" and instance:
        # Lock the workflow instance row to prevent concurrent approval races
        from app.models.workflow import WorkflowInstance
        from app.services.approval_chain import init_chain_state, resolve_applicable_levels

        locked_result = await db.execute(
            select(WorkflowInstance).where(WorkflowInstance.id == instance.id).with_for_update()
        )
        instance = locked_result.scalar_one()

        # Initialize chain state on first approval if not yet initialized
        if not (instance.state_data or {}).get("approval_levels"):
            from app.services.approval_chain import invoice_routing_attrs

            applicable = resolve_applicable_levels(
                approval_config.get("approval_chain", []),
                float(invoice.amount or 0),
                invoice_attrs=invoice_routing_attrs(invoice),
            )
            if applicable:
                init_chain_state(instance, applicable)
            else:
                # No levels apply — treat as single-level, fall through
                pass

        chain_complete = advance_approval_chain(instance, actor_id)
        if not chain_complete:
            # More levels needed — stay in ready_for_review, record partial
            await db.flush()
            return invoice

    # All approvals satisfied (or single-level) — finalize
    invoice.approval_date = date.today()
    invoice.approved_by = actor_name

    await transition_invoice(
        db,
        invoice,
        InvoiceStatus.approved,
        actor_id=actor_id,
        action_name="invoice.approved",
        details={"changes": field_diff} if field_diff else None,
    )

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
            entity_id=invoice.entity_id,  # exception follows its invoice (P2)
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
    control_db: AsyncSession | None = None,
) -> None:
    from app.services.audit_dispatch import dispatch_audit

    # Check delegation — if reviewer is OOO, reassign to their delegate
    original_id = None
    if control_db:
        from app.services.approval_chain import resolve_assignee

        effective_id, original_id = await resolve_assignee(reviewer_id, control_db)
        if original_id:
            # Delegation active — look up delegate's name
            from app.models.user import User as UserModel

            delegate_result = await control_db.execute(
                select(UserModel).where(UserModel.id == effective_id)
            )
            delegate = delegate_result.scalar_one_or_none()
            if delegate:
                reviewer_id = effective_id
                reviewer_name = delegate.full_name

    invoice.assigned_to_id = reviewer_id
    invoice.assigned_to = reviewer_name

    instance = await get_workflow_instance(db, invoice.id)
    if not instance:
        return

    # Find the current review step and assign it
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
        if original_id:
            step.original_assigned_to = original_id

    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=actor_id,
        action="invoice.assigned_for_review",
        entity_type="invoice",
        entity_id=invoice.id,
        details={
            "reviewer_id": str(reviewer_id),
            **({"delegated_from": str(original_id)} if original_id else {}),
        },
    )

    # Best-effort notification to the (possibly delegated) reviewer. Assignment
    # is the one notifiable event that does not flow through transition_invoice,
    # so it's dispatched explicitly here. Never breaks the assignment.
    from app.models.notification import EVENT_INVOICE_ASSIGNED
    from app.services.notification_dispatch import notify_event
    from app.services.notification_templates import InvoiceContext

    # `notify_event` swallows its own template/recipient/email failures, but its
    # per-recipient `db.add(...)` is unguarded; this outer guard is the final
    # backstop so a session error there can't abort an otherwise-valid
    # assignment — mirrors the guard in workflow_engine.transition_invoice.
    try:
        await notify_event(
            db,
            correlation_id=invoice.correlation_id,
            organization_id=invoice.organization_id,
            event_type=EVENT_INVOICE_ASSIGNED,
            entity_id=invoice.id,
            recipient_user_ids=[reviewer_id],
            invoice_ctx=InvoiceContext(
                invoice_number=invoice.invoice_number,
                vendor_name=invoice.vendor_name,
                amount=invoice.amount,
                currency=invoice.currency or "USD",
            ),
            actor_id=actor_id,
        )
    except Exception:  # noqa: BLE001 — never let a notification bug break assignment
        _log.exception("assign_reviewer: notification dispatch failed for invoice=%s", invoice.id)
