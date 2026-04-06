"""Payment endpoints."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_org_id
from app.models.user import User
from app.tenant import get_tenant_db
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun
from app.models.virtual_card import VirtualCard, CardRebate
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


class CreatePaymentRunItem(BaseModel):
    invoice_id: str
    method: str = "ach"  # ach, wire, check, virtual_card


class CreatePaymentRunRequest(BaseModel):
    items: list[CreatePaymentRunItem] = Field(..., min_length=1)


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_payment_run(
    body: CreatePaymentRunRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Create a payment run from selected invoices."""
    # Validate invoices exist and are payable
    invoice_ids = [uuid.UUID(item.invoice_id) for item in body.items]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(invoice_ids)))
    invoices = {str(inv.id): inv for inv in result.scalars().all()}

    if len(invoices) != len(invoice_ids):
        raise HTTPException(status_code=404, detail="One or more invoices not found")

    total = Decimal("0")
    for item in body.items:
        inv = invoices[item.invoice_id]
        total += inv.amount

    # Create the run
    run = PaymentRun(
        organization_id=org_id,
        status="draft",
        total_amount=total,
        initiated_by=user.id,
    )
    db.add(run)
    await db.flush()

    # Create individual payments
    for item in body.items:
        inv = invoices[item.invoice_id]
        payment = Payment(
            invoice_id=inv.id,
            payment_run_id=run.id,
            amount=inv.amount,
            method=item.method,
            status="pending",
            correlation_id=inv.correlation_id,
        )
        db.add(payment)

    await db.commit()

    return {
        "id": str(run.id),
        "status": run.status,
        "total_amount": float(total),
        "payment_count": len(body.items),
        "message": f"Payment run created with {len(body.items)} payments totaling ${float(total):,.2f}",
    }


@router.get("/runs/{run_id}")
async def get_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Get a payment run with its individual payments."""
    result = await db.execute(select(PaymentRun).where(PaymentRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Payment run not found")

    # Get payments in this run with invoice details
    pay_result = await db.execute(
        select(Payment, Invoice)
        .outerjoin(Invoice, Payment.invoice_id == Invoice.id)
        .where(Payment.payment_run_id == run_id)
    )
    payments = [
        {
            "id": str(p.id),
            "invoice_id": str(p.invoice_id),
            "invoice_number": inv.invoice_number if inv else None,
            "vendor_name": inv.vendor_name if inv else None,
            "amount": float(p.amount),
            "method": p.method,
            "status": p.status,
            "reference": p.reference,
        }
        for p, inv in pay_result.all()
    ]

    return {
        "id": str(run.id),
        "status": run.status,
        "total_amount": float(run.total_amount) if run.total_amount else 0,
        "initiated_by": str(run.initiated_by) if run.initiated_by else None,
        "executed_at": run.executed_at.isoformat() if run.executed_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else "",
        "payments": payments,
    }


@router.post("/runs/{run_id}/execute")
async def execute_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Execute a draft payment run — marks all payments as processing then completed."""
    result = await db.execute(select(PaymentRun).where(PaymentRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Payment run not found")
    if run.status != "draft":
        raise HTTPException(status_code=409, detail=f"Can only execute 'draft' runs, not '{run.status}'")

    # Update run status
    run.status = "completed"
    run.executed_at = datetime.now(timezone.utc)

    # Mark all payments as completed and generate references
    pay_result = await db.execute(
        select(Payment).where(Payment.payment_run_id == run_id)
    )
    payments = pay_result.scalars().all()

    completed = 0
    for i, payment in enumerate(payments):
        payment.status = "completed"
        method_prefix = (payment.method or "PAY").upper()
        payment.reference = f"{method_prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{i+1:03d}"

        # Update invoice status to payment_scheduled
        inv_result = await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
        inv = inv_result.scalar_one_or_none()
        if inv and inv.status.value in ("approved", "sent_to_erp", "posted_in_erp"):
            inv.status = InvoiceStatus.payment_scheduled
        completed += 1

    await db.commit()

    # Async ERP sync — doesn't block the response
    from app.services.payment_erp_sync import dispatch_payment_sync
    await dispatch_payment_sync(run_id, uuid.UUID(str(run.organization_id)))

    return {
        "id": str(run.id),
        "status": "completed",
        "payments_completed": completed,
        "message": f"Payment run executed — {completed} payments completed. ERP sync in progress.",
    }


# ── Payment Queue ───────────────────────────────────────────────────


@router.get("/queue")
async def payment_queue(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """List approved invoices ready for payment (no completed payment yet)."""
    # Subquery: invoices that already have a completed payment
    paid_ids = (
        select(Payment.invoice_id)
        .where(Payment.status == "completed")
        .scalar_subquery()
    )

    payable_statuses = [
        InvoiceStatus.approved.value,
        InvoiceStatus.sent_to_erp.value,
        InvoiceStatus.posted_in_erp.value,
        InvoiceStatus.payment_scheduled.value,
    ]

    result = await db.execute(
        select(Invoice)
        .where(
            Invoice.status.in_(payable_statuses),
            Invoice.id.notin_(paid_ids),
        )
        .order_by(Invoice.due_date.asc().nulls_last())
    )
    invoices = result.scalars().all()

    return {
        "items": [
            {
                "id": str(inv.id),
                "invoice_number": inv.invoice_number,
                "vendor_name": inv.vendor_name,
                "amount": float(inv.amount),
                "currency": inv.currency,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "payment_terms": inv.payment_terms,
                "status": inv.status.value if hasattr(inv.status, 'value') else inv.status,
                "is_overdue": inv.due_date is not None and inv.due_date < __import__("datetime").date.today(),
            }
            for inv in invoices
        ],
        "total": len(invoices),
        "total_amount": float(sum(inv.amount for inv in invoices)),
    }


# ── Payment Summary ─────────────────────────────────────────────────


@router.get("/summary")
async def payment_summary(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """KPIs for the payments page summary bar."""
    # Total paid (completed)
    paid_q = select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "completed")
    total_paid = float((await db.execute(paid_q)).scalar() or 0)

    # Total pending
    pending_q = select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status.in_(["pending", "processing"]))
    total_pending = float((await db.execute(pending_q)).scalar() or 0)

    # Payment count
    count_q = select(func.count()).select_from(Payment)
    payment_count = (await db.execute(count_q)).scalar() or 0

    # Rebates earned
    rebate_q = select(func.coalesce(func.sum(CardRebate.amount), 0))
    total_rebates = float((await db.execute(rebate_q)).scalar() or 0)

    # Queue size
    paid_ids = select(Payment.invoice_id).where(Payment.status == "completed").scalar_subquery()
    payable_statuses = [
        InvoiceStatus.approved.value,
        InvoiceStatus.sent_to_erp.value,
        InvoiceStatus.posted_in_erp.value,
        InvoiceStatus.payment_scheduled.value,
    ]
    queue_q = select(func.count()).select_from(
        select(Invoice.id).where(
            Invoice.status.in_(payable_statuses),
            Invoice.id.notin_(paid_ids),
        ).subquery()
    )
    queue_count = (await db.execute(queue_q)).scalar() or 0

    return {
        "total_paid": total_paid,
        "total_pending": total_pending,
        "payment_count": payment_count,
        "total_rebates": total_rebates,
        "queue_count": queue_count,
    }
