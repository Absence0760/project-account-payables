"""Payment endpoints."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tenant import get_tenant_db
from app.models.invoice import Invoice
from app.models.payment import Payment, PaymentRun
from app.schemas.payment import (
    PaymentCreate,
    PaymentListResponse,
    PaymentResponse,
    PaymentRunListResponse,
    PaymentRunResponse,
)

router = APIRouter(prefix="/payments", tags=["payments"])


# ── Individual Payments ──────────────────────────────────────────────


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    method: str | None = None,
    invoice_id: str | None = None,
    search: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    db: AsyncSession = Depends(get_tenant_db),
):
    query = select(Payment, Invoice).outerjoin(
        Invoice, Payment.invoice_id == Invoice.id
    )

    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(Payment.status.in_(statuses))
    if method:
        query = query.where(Payment.method == method)
    if invoice_id:
        query = query.where(Payment.invoice_id == uuid.UUID(invoice_id))
    if amount_min is not None:
        query = query.where(Payment.amount >= Decimal(str(amount_min)))
    if amount_max is not None:
        query = query.where(Payment.amount <= Decimal(str(amount_max)))
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Invoice.vendor_name.ilike(pattern)
            | Invoice.invoice_number.ilike(pattern)
            | Payment.reference.ilike(pattern)
        )

    # Count
    count_q = select(func.count()).select_from(
        select(Payment.id)
        .outerjoin(Invoice, Payment.invoice_id == Invoice.id)
        .where(query.whereclause) if query.whereclause is not None
        else select(Payment.id)
    )
    # Simpler count approach
    count_base = select(Payment)
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        count_base = count_base.where(Payment.status.in_(statuses))
    if method:
        count_base = count_base.where(Payment.method == method)
    if invoice_id:
        count_base = count_base.where(Payment.invoice_id == uuid.UUID(invoice_id))
    if amount_min is not None:
        count_base = count_base.where(Payment.amount >= Decimal(str(amount_min)))
    if amount_max is not None:
        count_base = count_base.where(Payment.amount <= Decimal(str(amount_max)))
    if search:
        pattern = f"%{search}%"
        count_base = count_base.outerjoin(Invoice, Payment.invoice_id == Invoice.id).where(
            Invoice.vendor_name.ilike(pattern)
            | Invoice.invoice_number.ilike(pattern)
            | Payment.reference.ilike(pattern)
        )

    total_q = select(func.count()).select_from(count_base.subquery())
    total = (await db.execute(total_q)).scalar() or 0

    # Paginate
    query = query.order_by(Payment.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    return PaymentListResponse(
        items=[PaymentResponse.from_db(p, inv) for p, inv in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(Payment, Invoice)
        .outerjoin(Invoice, Payment.invoice_id == Invoice.id)
        .where(Payment.id == payment_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Payment not found")
    p, inv = row
    return PaymentResponse.from_db(p, inv)


@router.post("", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment(
    body: PaymentCreate,
    db: AsyncSession = Depends(get_tenant_db),
):
    # Verify invoice exists
    inv_result = await db.execute(
        select(Invoice).where(Invoice.id == uuid.UUID(body.invoice_id))
    )
    invoice = inv_result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    payment = Payment(
        invoice_id=uuid.UUID(body.invoice_id),
        amount=body.amount,
        method=body.method.value if body.method else None,
        reference=body.reference,
        payment_run_id=uuid.UUID(body.payment_run_id) if body.payment_run_id else None,
        correlation_id=uuid.uuid4(),
    )
    db.add(payment)
    await db.flush()
    await db.refresh(payment)
    return PaymentResponse.from_db(payment, invoice)


# ── Payment Runs ─────────────────────────────────────────────────────


@router.get("/runs/", response_model=PaymentRunListResponse)
async def list_payment_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_tenant_db),
):
    query = select(PaymentRun)

    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(PaymentRun.status.in_(statuses))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(PaymentRun.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    runs = result.scalars().all()

    # Get payment counts per run
    items = []
    for run in runs:
        count_result = await db.execute(
            select(func.count()).where(Payment.payment_run_id == run.id)
        )
        count = count_result.scalar() or 0
        items.append(PaymentRunResponse.from_db(run, count))

    return PaymentRunListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
