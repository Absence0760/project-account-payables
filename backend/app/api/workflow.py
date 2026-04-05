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
from app.services.erp_dispatch import dispatch_erp
from app.services.extraction_dispatch import dispatch_extraction
from app.services.storage import get_file, upload_invoice_file
from app.services.workflow_engine import (
    create_workflow_instance,
    create_workflow_step,
    get_invoice_for_update,
    is_step_enabled,
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
    """Upload an invoice file, create the invoice, and optionally trigger extraction."""
    try:
        # Create invoice with blank fields
        invoice = Invoice(
            invoice_number="",
            vendor_name="",
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

        # Create workflow instance
        instance = await create_workflow_instance(db, invoice)

        # Check if extraction is enabled in the active workflow
        extraction_enabled = await is_step_enabled(db, org_id, "extraction")

        if extraction_enabled:
            # Transition new → pending and trigger extraction
            await transition_invoice(
                db,
                invoice,
                InvoiceStatus.pending,
                actor_id=user.id,
                action_name="invoice.uploaded",
                details={"filename": file.filename, "content_type": file.content_type},
            )
            await create_workflow_step(db, instance, "upload")
            await db.commit()
            await db.refresh(invoice)

            await dispatch_extraction(invoice.id, org_id, user.id)

            return {
                "id": str(invoice.id),
                "correlation_id": str(invoice.correlation_id),
                "status": invoice.status.value,
                "message": "Invoice uploaded. Extraction in progress.",
            }
        else:
            # No extraction — leave as new for manual entry
            await create_workflow_step(db, instance, "upload")
            await db.commit()
            await db.refresh(invoice)

            return {
                "id": str(invoice.id),
                "correlation_id": str(invoice.correlation_id),
                "status": invoice.status.value,
                "message": "Invoice uploaded. Ready for manual entry.",
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
    org_id: uuid.UUID = Depends(get_org_id),
):
    invoice = await get_invoice_for_update(db, invoice_id)

    # Transition to sending_to_erp before dispatching
    await transition_invoice(
        db,
        invoice,
        InvoiceStatus.sending_to_erp,
        actor_id=user.id,
        action_name="invoice.erp_submitted",
    )
    await db.commit()

    # Dispatch ERP call — local background task or SQS depending on config
    await dispatch_erp(invoice.id, org_id, user.id)

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
    org_id: uuid.UUID = Depends(get_org_id),
):
    invoice = await get_invoice_for_update(db, invoice_id)

    await erp_svc.retry_erp(db, invoice, actor_id=user.id)
    await db.commit()

    # Dispatch the retried ERP call
    await dispatch_erp(invoice.id, org_id, user.id)

    return {
        "id": str(invoice.id),
        "correlation_id": str(invoice.correlation_id),
        "status": invoice.status.value,
    }


# ---------- Stage 4: Complete ----------


@router.post("/{invoice_id}/complete", status_code=status.HTTP_200_OK)
async def complete_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Advance an invoice to the next logical step based on the workflow.

    - new + approval enabled → ready_for_review
    - new + no approval → done
    - approved + ERP enabled → triggers ERP dispatch
    - approved + no ERP → done
    """
    invoice = await get_invoice_for_update(db, invoice_id)

    # Validate required fields
    missing = []
    if not invoice.vendor_name or not invoice.vendor_name.strip():
        missing.append("vendor")
    if not invoice.invoice_number or not invoice.invoice_number.strip():
        missing.append("invoice_number")
    if invoice.amount is None or invoice.amount <= 0:
        missing.append("amount")
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Required fields missing: {', '.join(missing)}",
        )

    # Check workflow config for this invoice
    approval_enabled = await is_step_enabled(
        db, org_id, "approval", invoice_id=invoice.id
    )
    erp_enabled = await is_step_enabled(
        db, org_id, "erp_export", invoice_id=invoice.id
    )

    if invoice.status == InvoiceStatus.new and approval_enabled:
        # Submit for review
        await transition_invoice(
            db, invoice, InvoiceStatus.ready_for_review,
            actor_id=user.id, action_name="invoice.submitted_for_review",
        )
        await db.commit()
        await db.refresh(invoice)
        return {
            "id": str(invoice.id),
            "correlation_id": str(invoice.correlation_id),
            "status": invoice.status.value,
            "message": "Submitted for review.",
        }

    if invoice.status == InvoiceStatus.approved and erp_enabled:
        # Trigger ERP dispatch
        await transition_invoice(
            db, invoice, InvoiceStatus.sending_to_erp,
            actor_id=user.id, action_name="invoice.erp_submitted",
        )
        await db.commit()
        await dispatch_erp(invoice.id, org_id, user.id)
        return {
            "id": str(invoice.id),
            "correlation_id": str(invoice.correlation_id),
            "status": invoice.status.value,
            "message": "Sending to ERP.",
        }

    # Default: mark as done
    await transition_invoice(
        db, invoice, InvoiceStatus.done,
        actor_id=user.id, action_name="invoice.completed",
    )
    await db.commit()
    await db.refresh(invoice)
    return {
        "id": str(invoice.id),
        "correlation_id": str(invoice.correlation_id),
        "status": invoice.status.value,
        "message": "Invoice complete.",
    }


@router.get("/{invoice_id}/export")
async def export_invoice(
    invoice_id: uuid.UUID,
    format: str = "json",
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Export invoice data in the requested format for ERP upload."""
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    data = {
        "invoice_number": invoice.invoice_number,
        "vendor": invoice.vendor_name,
        "amount": str(invoice.amount),
        "currency": invoice.currency,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "po_number": invoice.po_number,
        "description": invoice.description,
        "subtotal": str(invoice.subtotal) if invoice.subtotal else None,
        "tax_amount": str(invoice.tax_amount) if invoice.tax_amount else None,
        "gl_account": invoice.gl_account,
        "cost_center": invoice.cost_center,
        "correlation_id": str(invoice.correlation_id),
    }

    if format == "xml":
        import xml.etree.ElementTree as ET

        root = ET.Element("Invoice")
        for key, value in data.items():
            child = ET.SubElement(root, key)
            child.text = value if value is not None else ""
        content = ET.tostring(root, encoding="unicode", xml_declaration=True)

        from fastapi.responses import Response
        return Response(
            content=content,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="invoice-{invoice.invoice_number or invoice_id}.xml"'},
        )

    elif format == "csv":
        import csv
        import io

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=data.keys())
        writer.writeheader()
        writer.writerow(data)

        from fastapi.responses import Response
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="invoice-{invoice.invoice_number or invoice_id}.csv"'},
        )

    else:
        # JSON (default)
        return data


# ---------- File access ----------


@router.get("/file/{file_key:path}")
async def get_invoice_file(file_key: str):
    """Proxy the file from S3 to the browser."""
    from fastapi.responses import Response

    try:
        content, content_type = get_file(file_key)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")

    return Response(content=content, media_type=content_type)


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
