"""Invoice CRUD endpoints."""

import csv
import io
import uuid
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exception import Exception as ExceptionModel
from app.models.invoice import Invoice, InvoiceExtractionResult, InvoiceLineItem
from app.models.invoice import InvoiceStatus as DBInvoiceStatus
from app.models.payment import Payment, PaymentSchedule
from app.models.workflow import WorkflowInstance, WorkflowStep
from app.schemas.invoice import (
    BulkDeleteRequest,
    BulkDeleteResponse,
    BulkExportRequest,
    BulkStatusRequest,
    BulkStatusResponse,
    InvoiceCreate,
    InvoiceListResponse,
    InvoiceResponse,
    InvoiceUpdate,
)
from app.services.invoice_warnings import refresh_warnings
from app.tenant import get_tenant_db

IMMUTABLE_STATUSES = {
    DBInvoiceStatus.sending_to_erp,
    DBInvoiceStatus.sent_to_erp,
    DBInvoiceStatus.posted_in_erp,
    DBInvoiceStatus.payment_scheduled,
    DBInvoiceStatus.paid,
    DBInvoiceStatus.done,
}

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: str | None = None,
    vendor: str | None = None,
    invoice_number: str | None = None,
    po_number: str | None = None,
    description: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    due_date_from: date | None = None,
    due_date_to: date | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
):
    query = select(Invoice)

    # Filters
    if status:
        statuses = [s.strip() for s in status.split(",")]
        query = query.where(Invoice.status.in_(statuses))
    if vendor:
        query = query.where(Invoice.vendor_name.ilike(f"%{vendor}%"))
    if invoice_number:
        query = query.where(Invoice.invoice_number.ilike(f"%{invoice_number}%"))
    if po_number:
        query = query.where(Invoice.po_number.ilike(f"%{po_number}%"))
    if description:
        query = query.where(Invoice.description.ilike(f"%{description}%"))
    if amount_min is not None:
        query = query.where(Invoice.amount >= Decimal(str(amount_min)))
    if amount_max is not None:
        query = query.where(Invoice.amount <= Decimal(str(amount_max)))
    if due_date_from:
        query = query.where(Invoice.due_date >= due_date_from)
    if due_date_to:
        query = query.where(Invoice.due_date <= due_date_to)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Invoice.vendor_name.ilike(pattern)
            | Invoice.invoice_number.ilike(pattern)
            | Invoice.po_number.ilike(pattern)
            | Invoice.description.ilike(pattern)
        )

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.order_by(Invoice.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    invoices = result.scalars().all()

    return InvoiceListResponse(
        items=[InvoiceResponse.from_db(inv) for inv in invoices],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return InvoiceResponse.from_db(invoice)


@router.get("/{invoice_id}/line-items")
async def get_invoice_line_items(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    from app.models.invoice import InvoiceLineItem

    result = await db.execute(
        select(InvoiceLineItem)
        .where(InvoiceLineItem.invoice_id == invoice_id)
        .order_by(InvoiceLineItem.line_number)
    )
    items = result.scalars().all()
    return [
        {
            "id": str(li.id),
            "line_number": li.line_number,
            "item_code": li.item_code,
            "description": li.description,
            "quantity": float(li.quantity) if li.quantity else None,
            "unit_price": float(li.unit_price) if li.unit_price else None,
            "tax": float(li.tax) if li.tax else None,
            "total": float(li.total) if li.total else None,
            "gl_account": li.gl_account,
        }
        for li in items
    ]


@router.put("/{invoice_id}/line-items")
async def save_invoice_line_items(
    invoice_id: uuid.UUID,
    body: list[dict],
    db: AsyncSession = Depends(get_tenant_db),
):
    """Replace all line items for an invoice."""
    from app.models.invoice import InvoiceLineItem

    # Verify invoice exists
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Delete existing line items
    await db.execute(sa_delete(InvoiceLineItem).where(InvoiceLineItem.invoice_id == invoice_id))

    # Insert new line items
    for i, item in enumerate(body):
        li = InvoiceLineItem(
            invoice_id=invoice_id,
            line_number=item.get("line_number", i + 1),
            item_code=item.get("item_code"),
            description=item.get("description"),
            quantity=Decimal(str(item["quantity"])) if item.get("quantity") is not None else None,
            unit_price=Decimal(str(item["unit_price"]))
            if item.get("unit_price") is not None
            else None,
            tax=Decimal(str(item["tax"])) if item.get("tax") is not None else None,
            total=Decimal(str(item["total"])) if item.get("total") is not None else None,
            gl_account=item.get("gl_account"),
        )
        db.add(li)

    await db.commit()
    return {"saved": len(body)}


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    body: InvoiceCreate,
    db: AsyncSession = Depends(get_tenant_db),
):
    invoice = Invoice(
        invoice_number=body.invoice_number,
        vendor_name=body.vendor,
        description=body.description,
        amount=body.amount,
        currency=body.currency,
        invoice_date=body.invoice_date,
        received_date=body.received_date,
        due_date=body.due_date,
        payment_terms=body.payment_terms,
        status=body.status.value,
        po_number=body.po_number,
        subtotal=body.subtotal,
        tax_amount=body.tax_amount,
        discount_amount=body.discount_amount,
        shipping_amount=body.shipping_amount,
        remit_to_address=body.remit_to_address,
        bill_to_address=body.bill_to_address,
        vendor_address=body.vendor_address,
        vendor_tax_id=body.vendor_tax_id,
        ship_to_address=body.ship_to_address,
        tax_rate=body.tax_rate,
        payment_method=body.payment_method,
        reference_number=body.reference_number,
        notes=body.notes,
        gl_account=body.gl_account,
        cost_center=body.cost_center,
    )
    db.add(invoice)
    await db.flush()
    await db.refresh(invoice)
    return InvoiceResponse.from_db(invoice)


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceUpdate,
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status in IMMUTABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Cannot update invoice in this status")

    update_data = body.model_dump(exclude_unset=True)
    # Map frontend field name to DB column
    if "vendor" in update_data:
        update_data["vendor_name"] = update_data.pop("vendor")
    if "status" in update_data and update_data["status"] is not None:
        update_data["status"] = update_data["status"].value

    for field, value in update_data.items():
        setattr(invoice, field, value)

    await refresh_warnings(db, invoice)
    await db.flush()
    await db.refresh(invoice)
    return InvoiceResponse.from_db(invoice)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status in IMMUTABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Cannot delete invoice in this status")
    await _delete_invoice_cascade(db, invoice_id)
    await db.commit()


# ---------- Helpers ----------


async def _delete_invoice_cascade(db: AsyncSession, invoice_id: uuid.UUID) -> None:
    """Delete an invoice and all related records across tables."""
    # Delete workflow steps (child of workflow_instances)
    wf_ids_q = select(WorkflowInstance.id).where(WorkflowInstance.invoice_id == invoice_id)
    await db.execute(sa_delete(WorkflowStep).where(WorkflowStep.instance_id.in_(wf_ids_q)))
    # Delete direct children of invoices
    for model in (
        ExceptionModel,
        Payment,
        PaymentSchedule,
        WorkflowInstance,
        InvoiceExtractionResult,
        InvoiceLineItem,
    ):
        await db.execute(sa_delete(model).where(model.invoice_id == invoice_id))
    await db.execute(sa_delete(Invoice).where(Invoice.id == invoice_id))


# ---------- Bulk operations ----------


def _invoice_to_export_dict(inv: Invoice) -> dict:
    return {
        "invoice_number": inv.invoice_number,
        "vendor": inv.vendor_name,
        "amount": str(inv.amount),
        "currency": inv.currency,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "po_number": inv.po_number,
        "description": inv.description,
        "subtotal": str(inv.subtotal) if inv.subtotal else None,
        "tax_amount": str(inv.tax_amount) if inv.tax_amount else None,
        "gl_account": inv.gl_account,
        "cost_center": inv.cost_center,
        "correlation_id": str(inv.correlation_id),
    }


@router.post("/bulk/delete", response_model=BulkDeleteResponse)
async def bulk_delete(
    body: BulkDeleteRequest,
    db: AsyncSession = Depends(get_tenant_db),
):
    ids = [uuid.UUID(i) for i in body.ids]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(ids)))
    invoices = result.scalars().all()

    deleted = 0
    skipped: list[str] = []
    for inv in invoices:
        if inv.status in IMMUTABLE_STATUSES:
            skipped.append(str(inv.id))
        else:
            await _delete_invoice_cascade(db, inv.id)
            deleted += 1

    await db.commit()
    return BulkDeleteResponse(deleted=deleted, skipped=skipped)


@router.post("/bulk/status", response_model=BulkStatusResponse)
async def bulk_status_change(
    body: BulkStatusRequest,
    db: AsyncSession = Depends(get_tenant_db),
):
    ids = [uuid.UUID(i) for i in body.ids]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(ids)))
    invoices = result.scalars().all()

    updated = 0
    skipped: list[str] = []
    for inv in invoices:
        if inv.status in IMMUTABLE_STATUSES:
            skipped.append(str(inv.id))
        else:
            inv.status = body.status.value
            await refresh_warnings(db, inv)
            updated += 1
    await db.commit()

    return BulkStatusResponse(updated=updated, skipped=skipped)


@router.post("/bulk/export")
async def bulk_export(
    body: BulkExportRequest,
    db: AsyncSession = Depends(get_tenant_db),
):
    ids = [uuid.UUID(i) for i in body.ids]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(ids)))
    invoices = result.scalars().all()

    if not invoices:
        raise HTTPException(status_code=404, detail="No invoices found")

    rows = [_invoice_to_export_dict(inv) for inv in invoices]

    if body.format == "xml":
        root = ET.Element("Invoices")
        for row in rows:
            inv_el = ET.SubElement(root, "Invoice")
            for key, value in row.items():
                child = ET.SubElement(inv_el, key)
                child.text = value if value is not None else ""
        content = ET.tostring(root, encoding="unicode", xml_declaration=True)
        return Response(
            content=content,
            media_type="application/xml",
            headers={"Content-Disposition": 'attachment; filename="invoices-export.xml"'},
        )

    elif body.format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="invoices-export.csv"'},
        )

    else:
        return rows
