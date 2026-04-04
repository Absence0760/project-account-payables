"""Invoice CRUD endpoints."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.api.deps import get_org_id
from app.models.invoice import Invoice, InvoiceStatus as DBInvoiceStatus
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceListResponse,
    InvoiceResponse,
    InvoiceUpdate,
)

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
    org_id: uuid.UUID = Depends(get_org_id),
    db: AsyncSession = Depends(get_db),
):
    query = select(Invoice).where(Invoice.organization_id == org_id)

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
    org_id: uuid.UUID = Depends(get_org_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.organization_id == org_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return InvoiceResponse.from_db(invoice)


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    body: InvoiceCreate,
    org_id: uuid.UUID = Depends(get_org_id),
    db: AsyncSession = Depends(get_db),
):
    invoice = Invoice(
        organization_id=org_id,
        invoice_number=body.invoice_number,
        vendor_name=body.vendor,
        description=body.description,
        amount=body.amount,
        currency=body.currency,
        due_date=body.due_date,
        status=body.status.value,
        po_number=body.po_number,
    )
    db.add(invoice)
    await db.flush()
    await db.refresh(invoice)
    return InvoiceResponse.from_db(invoice)


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceUpdate,
    org_id: uuid.UUID = Depends(get_org_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.organization_id == org_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    update_data = body.model_dump(exclude_unset=True)
    # Map frontend field name to DB column
    if "vendor" in update_data:
        update_data["vendor_name"] = update_data.pop("vendor")
    if "status" in update_data and update_data["status"] is not None:
        update_data["status"] = update_data["status"].value

    for field, value in update_data.items():
        setattr(invoice, field, value)

    await db.flush()
    await db.refresh(invoice)
    return InvoiceResponse.from_db(invoice)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_org_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.organization_id == org_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await db.delete(invoice)
