"""Workflow action endpoints — upload, review, ERP, and read endpoints."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_org_id
from app.models.invoice import Invoice, InvoiceStatus, InvoiceExtractionResult
from app.models.user import User
from app.models.workflow import AuditLog, WorkflowInstance, WorkflowStep
from app.schemas.invoice import InvoiceResponse
from app.schemas.workflow import (
    ApproveRequest,
    AssignReviewerRequest,
    AuditLogEntryResponse,
    RejectRequest,
    WorkflowInstanceResponse,
    WorkflowStepResponse,
)
from app.services import review as review_svc
from app.services import erp as erp_svc
from app.services.extraction_dispatch import dispatch_extraction
from app.services.storage import upload_invoice_file
from app.services.workflow_engine import (
    create_workflow_instance,
    create_workflow_step,
    get_invoice_for_update,
    transition_invoice,
)
from app.tenant import get_tenant_db

router = APIRouter(prefix="/invoices", tags=["workflow"])


# ---------- Stage 1: Upload ----------


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_invoice(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Upload an invoice file, create the invoice, and trigger AI extraction."""
    # Validate file
    try:
        # Create invoice with placeholder fields
        invoice = Invoice(
            invoice_number="PENDING",
            vendor_name="PENDING",
            description="",
            amount=Decimal("0"),
            currency="USD",
            status=InvoiceStatus.new,
            organization_id=org_id,
        )
        db.add(invoice)
        await db.flush()

        # Upload file to S3
        file_key, file_url = await upload_invoice_file(org_id, invoice.id, file)
        invoice.file_key = file_key
        invoice.file_url = file_url

        # Transition new → pending
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.pending,
            actor_id=user.id,
            action_name="invoice.uploaded",
            details={"filename": file.filename, "content_type": file.content_type},
        )

        # Create workflow instance and first step
        instance = await create_workflow_instance(db, invoice)
        await create_workflow_step(db, instance, "upload")

        await db.commit()
        await db.refresh(invoice)

        # Trigger extraction — local background task or SQS depending on config
        await dispatch_extraction(invoice.id, org_id, user.id)

        return {
            "id": str(invoice.id),
            "correlation_id": str(invoice.correlation_id),
            "status": invoice.status.value,
            "message": "Invoice uploaded. Extraction in progress.",
        }

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------- Stage 2: Review ----------


@router.post("/{invoice_id}/assign", response_model=InvoiceResponse)
async def assign_reviewer(
    invoice_id: uuid.UUID,
    body: AssignReviewerRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    invoice = await get_invoice_for_update(db, invoice_id)
    if invoice.status != InvoiceStatus.ready_for_review:
        raise HTTPException(status_code=409, detail="Invoice must be in 'ready_for_review' to assign a reviewer")

    await review_svc.assign_reviewer(
        db,
        invoice,
        actor_id=user.id,
        reviewer_id=uuid.UUID(body.user_id),
    )
    return InvoiceResponse.from_db(invoice)


@router.post("/{invoice_id}/approve", response_model=InvoiceResponse)
async def approve_invoice(
    invoice_id: uuid.UUID,
    body: ApproveRequest | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    invoice = await get_invoice_for_update(db, invoice_id)
    corrections = body.model_dump(exclude_unset=True) if body else None

    await review_svc.approve_invoice(
        db,
        invoice,
        actor_id=user.id,
        actor_name=user.full_name,
        corrections=corrections or None,
    )
    return InvoiceResponse.from_db(invoice)


@router.post("/{invoice_id}/reject", response_model=InvoiceResponse)
async def reject_invoice(
    invoice_id: uuid.UUID,
    body: RejectRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    invoice = await get_invoice_for_update(db, invoice_id)

    await review_svc.reject_invoice(
        db,
        invoice,
        actor_id=user.id,
        reason=body.reason,
    )
    return InvoiceResponse.from_db(invoice)


@router.post("/{invoice_id}/resubmit", response_model=InvoiceResponse)
async def resubmit_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    invoice = await get_invoice_for_update(db, invoice_id)

    await review_svc.resubmit_invoice(
        db,
        invoice,
        actor_id=user.id,
    )
    return InvoiceResponse.from_db(invoice)


# ---------- Stage 3: ERP ----------


@router.post("/{invoice_id}/send-to-erp", status_code=status.HTTP_202_ACCEPTED)
async def send_to_erp(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    invoice = await get_invoice_for_update(db, invoice_id)

    # send_to_erp handles its own commits (for retry logic)
    await erp_svc.send_to_erp(db, invoice, actor_id=user.id)
    await db.refresh(invoice)

    return {
        "id": str(invoice.id),
        "correlation_id": str(invoice.correlation_id),
        "status": invoice.status.value,
    }


@router.post("/{invoice_id}/retry-erp", status_code=status.HTTP_202_ACCEPTED)
async def retry_erp(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    invoice = await get_invoice_for_update(db, invoice_id)

    await erp_svc.retry_erp(db, invoice, actor_id=user.id)
    await db.refresh(invoice)

    return {
        "id": str(invoice.id),
        "correlation_id": str(invoice.correlation_id),
        "status": invoice.status.value,
    }


# ---------- Read endpoints ----------


@router.get("/{invoice_id}/workflow")
async def get_workflow(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(WorkflowInstance).where(WorkflowInstance.invoice_id == invoice_id)
    )
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="No workflow found for this invoice")

    steps_result = await db.execute(
        select(WorkflowStep)
        .where(WorkflowStep.instance_id == instance.id)
        .order_by(WorkflowStep.step_number, WorkflowStep.created_at)
    )
    steps = steps_result.scalars().all()

    return WorkflowInstanceResponse.from_db(instance, steps)


@router.get("/{invoice_id}/audit-log")
async def get_audit_log(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    # Get the invoice's correlation_id
    result = await db.execute(
        select(Invoice.correlation_id).where(Invoice.id == invoice_id)
    )
    correlation_id = result.scalar_one_or_none()
    if not correlation_id:
        raise HTTPException(status_code=404, detail="Invoice not found")

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.correlation_id == correlation_id)
        .order_by(AuditLog.created_at)
    )
    entries = result.scalars().all()

    return [AuditLogEntryResponse.from_db(e) for e in entries]


@router.get("/{invoice_id}/extraction")
async def get_extraction_results(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(InvoiceExtractionResult)
        .where(InvoiceExtractionResult.invoice_id == invoice_id)
        .order_by(InvoiceExtractionResult.created_at.desc())
    )
    results = result.scalars().all()

    return [
        {
            "id": str(r.id),
            "method": r.method,
            "confidence": float(r.confidence) if r.confidence else None,
            "raw_result": r.raw_result,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in results
    ]
