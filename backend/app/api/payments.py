"""Payment endpoints."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.database import get_control_db
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.payment import Payment, PaymentRun, PaymentSchedule
from app.models.user import User
from app.models.vendor import Vendor
from app.models.virtual_card import CardRebate
from app.schemas.payment import (
    PaymentCreate,
    PaymentListResponse,
    PaymentResponse,
    PaymentRunListResponse,
    PaymentRunResponse,
)
from app.services.payment_adapters import (
    PaymentPayload,
    PaymentStatus,
    get_payment_adapter,
)
from app.tenant import get_tenant, get_tenant_db

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
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    query = select(Payment, Invoice).outerjoin(Invoice, Payment.invoice_id == Invoice.id)

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
    select(func.count()).select_from(
        select(Payment.id)
        .outerjoin(Invoice, Payment.invoice_id == Invoice.id)
        .where(query.whereclause)
        if query.whereclause is not None
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


# NOTE: /queue and /summary MUST be declared before the /{payment_id} route —
# FastAPI matches paths in declaration order, and "queue"/"summary" otherwise
# get parsed as a UUID and fail with a 422 before ever reaching the handler.


@router.get("/queue")
async def payment_queue(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """List approved invoices ready for payment (no completed payment yet)."""
    paid_ids = select(Payment.invoice_id).where(Payment.status == "completed").scalar_subquery()

    payable_statuses = [
        InvoiceStatus.approved.value,
        InvoiceStatus.sent_to_erp.value,
        InvoiceStatus.posted_in_erp.value,
        InvoiceStatus.payment_scheduled.value,
    ]

    result = await db.execute(
        select(Invoice, PaymentSchedule)
        .outerjoin(PaymentSchedule, PaymentSchedule.invoice_id == Invoice.id)
        .where(
            Invoice.status.in_(payable_statuses),
            Invoice.id.notin_(paid_ids),
        )
        .order_by(Invoice.due_date.asc().nulls_last())
    )
    rows = result.all()

    today = date.today()
    items: list[dict] = []
    total_savings = Decimal("0")
    for inv, sched in rows:
        # Discount eligibility: schedule has a discount_date in the future
        # AND the percent is set. Backfilled-without-schedule rows just don't
        # surface a discount.
        discount_amount: Decimal | None = None
        discount_eligible = False
        if (
            sched is not None
            and sched.discount_date is not None
            and sched.discount_percent is not None
            and sched.discount_date >= today
        ):
            discount_eligible = True
            discount_amount = (inv.amount * sched.discount_percent / Decimal(100)).quantize(
                Decimal("0.01")
            )
            total_savings += discount_amount

        items.append(
            {
                "id": str(inv.id),
                "invoice_number": inv.invoice_number,
                "vendor_name": inv.vendor_name,
                "amount": float(inv.amount),
                "currency": inv.currency,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "payment_terms": inv.payment_terms,
                "status": inv.status.value if hasattr(inv.status, "value") else inv.status,
                "is_overdue": inv.due_date is not None and inv.due_date < today,
                # Discount surface — null when the row isn't eligible (no
                # schedule, no discount_date, or the discount window has
                # already passed).
                "discount_eligible": discount_eligible,
                "discount_date": sched.discount_date.isoformat()
                if sched and sched.discount_date
                else None,
                "discount_percent": float(sched.discount_percent)
                if sched and sched.discount_percent
                else None,
                "discount_amount": float(discount_amount) if discount_amount else None,
            }
        )

    return {
        "items": items,
        "total": len(items),
        "total_amount": float(sum(Decimal(str(it["amount"])) for it in items)),
        "total_savings": float(total_savings),
    }


@router.get("/summary")
async def payment_summary(
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """KPIs for the payments page summary bar."""
    paid_q = select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "completed")
    total_paid = float((await db.execute(paid_q)).scalar() or 0)

    pending_q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status.in_(["pending", "processing", "submitted"])
    )
    total_pending = float((await db.execute(pending_q)).scalar() or 0)

    count_q = select(func.count()).select_from(Payment)
    payment_count = (await db.execute(count_q)).scalar() or 0

    # CardRebate — table may not exist yet (virtual cards not provisioned).
    try:
        rebate_q = select(func.coalesce(func.sum(CardRebate.amount), 0))
        total_rebates = float((await control_db.execute(rebate_q)).scalar() or 0)
    except Exception:
        await control_db.rollback()
        total_rebates = 0.0

    paid_ids = select(Payment.invoice_id).where(Payment.status == "completed").scalar_subquery()
    payable_statuses = [
        InvoiceStatus.approved.value,
        InvoiceStatus.sent_to_erp.value,
        InvoiceStatus.posted_in_erp.value,
        InvoiceStatus.payment_scheduled.value,
    ]
    queue_q = select(func.count()).select_from(
        select(Invoice.id)
        .where(
            Invoice.status.in_(payable_statuses),
            Invoice.id.notin_(paid_ids),
        )
        .subquery()
    )
    queue_count = (await db.execute(queue_q)).scalar() or 0

    return {
        "total_paid": total_paid,
        "total_pending": total_pending,
        "payment_count": payment_count,
        "total_rebates": total_rebates,
        "queue_count": queue_count,
    }


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
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


class VoidPaymentRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


@router.post("/{payment_id}/void")
async def void_payment(
    payment_id: uuid.UUID,
    body: VoidPaymentRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
):
    """Void a completed or in-flight payment.

    Adapter dispatch: when the configured processor exposes
    ``void_payment`` we ask it to reverse upstream. If it doesn't (mock /
    legacy rows), we record the local void only — the AP team is
    expected to chase the rail manually. Either way, the invoice flips
    back to ``approved`` so it re-enters the payment queue.
    """
    result = await db.execute(
        select(Payment, Invoice)
        .outerjoin(Invoice, Payment.invoice_id == Invoice.id)
        .where(Payment.id == payment_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment, invoice = row

    if payment.status in ("voided", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Payment already {payment.status}")
    if payment.status == "failed":
        raise HTTPException(
            status_code=409,
            detail="Cannot void a failed payment (it never settled)",
        )

    # Adapter side: best-effort. A processor failure here doesn't block
    # the local void — operators can chase the rail manually, but the
    # accounting books should always reflect intent.
    payment_config = (org.settings or {}).get("payments") or {}
    adapter = get_payment_adapter(payment_config)
    adapter_outcome: str | None = None
    if payment.provider_payment_id:
        try:
            void_fn = getattr(adapter, "void_payment", None)
            if callable(void_fn):
                ok = await void_fn(payment.provider_payment_id)
                adapter_outcome = "voided_upstream" if ok else "rejected_by_processor"
            else:
                adapter_outcome = "no_adapter_support"
        except Exception as exc:  # noqa: BLE001
            adapter_outcome = f"adapter_error:{exc.__class__.__name__}"

    now = datetime.now(UTC)
    payment.status = "voided"
    payment.failure_reason = f"Voided by {user.full_name}: {body.reason}"
    payment.completed_at = now

    # Reopen the invoice for re-payment if it was scheduled by this row.
    if invoice and invoice.status in (
        InvoiceStatus.payment_scheduled,
        InvoiceStatus.paid,
    ):
        invoice.status = InvoiceStatus.approved

    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=payment.correlation_id or uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="payment.voided",
        entity_type="payment",
        entity_id=payment.id,
        details={
            "reason": body.reason,
            "adapter_outcome": adapter_outcome,
            "amount": float(payment.amount),
            "previous_status": "completed"
            if payment.completed_at and payment.completed_at <= now
            else (payment.status or "unknown"),
        },
    )
    await db.commit()
    await db.refresh(payment)
    return PaymentResponse.from_db(payment, invoice)


@router.post("", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment(
    body: PaymentCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    # Verify invoice exists
    inv_result = await db.execute(select(Invoice).where(Invoice.id == uuid.UUID(body.invoice_id)))
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
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
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
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
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
        "message": (
            f"Payment run created with {len(body.items)} payments totaling ${float(total):,.2f}"
        ),
    }


@router.get("/runs/{run_id}")
async def get_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
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


@router.post("/runs/{run_id}/cancel")
async def cancel_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """Cancel a draft run before it executes. Only valid from `draft`;
    flips the run to `cancelled` and removes its child payment rows so
    the invoices return to the queue. Audit-logged."""
    result = await db.execute(select(PaymentRun).where(PaymentRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Payment run not found")
    if run.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"Can only cancel 'draft' runs, not '{run.status}'",
        )

    pay_result = await db.execute(select(Payment).where(Payment.payment_run_id == run_id))
    payments = pay_result.scalars().all()
    invoice_ids = [p.invoice_id for p in payments]

    # Drop the placeholder payment rows so the invoices re-enter the queue.
    # The run itself stays in the table for history; status flips to
    # `cancelled` so list filters can exclude it without losing the audit
    # trail.
    for p in payments:
        await db.delete(p)
    run.status = "cancelled"

    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="payment_run.cancelled",
        entity_type="payment_run",
        entity_id=run.id,
        details={
            "invoice_ids": [str(i) for i in invoice_ids],
            "payment_count": len(payments),
            "total_amount": float(run.total_amount or 0),
        },
    )
    await db.commit()

    return {
        "id": str(run.id),
        "status": run.status,
        "released_invoices": len(invoice_ids),
        "message": (
            f"Draft run cancelled; {len(invoice_ids)} invoice(s) returned to the queue."
        ),
    }


@router.post("/runs/{run_id}/execute")
async def execute_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """Execute a draft payment run via the configured payment adapter.

    Each payment is dispatched to the org's configured processor (Modern
    Treasury for prod, mock for dev). The adapter returns either a
    `submitted`/`processing` status (real money in flight, terminal status
    arrives via webhook) or `completed`/`failed` immediately (mock).

    Run status:
      - `completed` — every payment reached `completed`
      - `partial`   — at least one succeeded, at least one failed
      - `failed`    — every payment failed
      - `submitted` — at least one payment is in flight (waiting on webhook)
    """
    result = await db.execute(select(PaymentRun).where(PaymentRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Payment run not found")
    if run.status != "draft":
        raise HTTPException(
            status_code=409, detail=f"Can only execute 'draft' runs, not '{run.status}'"
        )

    payment_config = (org.settings or {}).get("payments") or {}
    adapter = get_payment_adapter(payment_config)
    now = datetime.now(UTC)

    pay_result = await db.execute(select(Payment).where(Payment.payment_run_id == run_id))
    payments = pay_result.scalars().all()

    completed = 0
    failed = 0
    in_flight = 0

    for payment in payments:
        # Resolve invoice + vendor for the payload
        inv_result = await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
        invoice = inv_result.scalar_one_or_none()
        vendor_bank: dict | None = None
        if invoice and invoice.vendor_id:
            v_result = await db.execute(
                select(Vendor.bank_details).where(Vendor.id == invoice.vendor_id)
            )
            vendor_bank = v_result.scalar_one_or_none()

        payload = PaymentPayload(
            correlation_id=str(payment.correlation_id or payment.id),
            invoice_id=str(payment.invoice_id),
            invoice_number=invoice.invoice_number if invoice else "",
            vendor_name=invoice.vendor_name if invoice else "",
            amount=payment.amount,
            currency=invoice.currency if invoice else "USD",
            method=payment.method or "ach",
            description=invoice.description if invoice else None,
            vendor_bank=vendor_bank,
            metadata={"organization_id": str(org.id)},
        )

        result_obj = await adapter.create_payment(payload)
        payment.provider = adapter.provider_name
        payment.provider_payment_id = result_obj.provider_payment_id
        payment.reference = result_obj.reference or payment.reference
        payment.submitted_at = now

        if result_obj.status == PaymentStatus.completed:
            payment.status = "completed"
            payment.completed_at = now
            completed += 1
            if invoice and invoice.status.value in (
                "approved",
                "sent_to_erp",
                "posted_in_erp",
            ):
                invoice.status = InvoiceStatus.payment_scheduled
        elif result_obj.status in (PaymentStatus.submitted, PaymentStatus.processing):
            # Real money in flight; webhook will finalize.
            payment.status = result_obj.status.value
            in_flight += 1
            if invoice and invoice.status.value in (
                "approved",
                "sent_to_erp",
                "posted_in_erp",
            ):
                invoice.status = InvoiceStatus.payment_scheduled
        else:
            # failed or cancelled
            payment.status = result_obj.status.value
            payment.failure_reason = result_obj.failure_reason
            payment.completed_at = now
            failed += 1

    # Run status reflects the rollup of its payments.
    if failed and not (completed or in_flight):
        run.status = "failed"
    elif failed:
        run.status = "partial"
    elif in_flight:
        run.status = "submitted"
    else:
        run.status = "completed"
    run.executed_at = now

    await db.commit()

    # ERP sync only fires for payments we believe settled — pending ones
    # will sync when their webhook lands.
    if completed:
        from app.services.payment_erp_sync import dispatch_payment_sync

        await dispatch_payment_sync(run_id, uuid.UUID(str(run.organization_id)))

    return {
        "id": str(run.id),
        "status": run.status,
        "provider": adapter.provider_name,
        "payments_completed": completed,
        "payments_in_flight": in_flight,
        "payments_failed": failed,
        "message": _execute_message(adapter.provider_name, completed, in_flight, failed),
    }


def _execute_message(provider: str, completed: int, in_flight: int, failed: int) -> str:
    parts: list[str] = []
    if completed:
        parts.append(f"{completed} completed")
    if in_flight:
        parts.append(f"{in_flight} in flight")
    if failed:
        parts.append(f"{failed} failed")
    body = ", ".join(parts) or "0 payments"
    return f"Payment run executed via {provider}: {body}."


# ── Provider webhook ────────────────────────────────────────────────


@router.post("/webhook/{tenant_slug}/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def payment_webhook(tenant_slug: str, provider: str, request: Request):
    """Receive a payment-status webhook from the configured processor.

    Auth is by the processor's HMAC signature (verified inside
    `adapter.parse_webhook`), not a JWT. The tenant is encoded in the URL
    path — each tenant configures its own webhook URL with the processor,
    so a leaked URL only affects that one tenant. Bad signatures, unknown
    events, and unknown tenants all return 204 silently — leaking the
    distinction would help an attacker probe for the right secret. Audit
    log captures the rejection.

    URL shape (configure in the processor's dashboard):
        https://app.com/api/payments/webhook/{tenant_slug}/{provider}
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database import control_session_factory, get_tenant_engine

    body = await request.body()
    headers = {k: v for k, v in request.headers.items()}

    # Resolve tenant from the URL path (no JWT, no X-Tenant-Slug header).
    async with control_session_factory() as ctrl_db:
        org_result = await ctrl_db.execute(
            select(Organization).where(Organization.slug == tenant_slug)
        )
        org = org_result.scalar_one_or_none()
    if org is None:
        return

    payment_config = (org.settings or {}).get("payments") or {}
    if payment_config.get("provider") != provider:
        return  # wrong adapter for this tenant

    adapter = get_payment_adapter(payment_config)
    event = adapter.parse_webhook(headers, body)
    if event is None:
        return  # bad signature, unrecognised event, or no-op

    # Open a tenant-DB session to look up + update the Payment row.
    engine = get_tenant_engine(org.db_name)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        pay_result = await db.execute(
            select(Payment).where(Payment.provider_payment_id == event.provider_payment_id)
        )
        payment = pay_result.scalar_one_or_none()
        if not payment:
            return  # late retry of a payment we don't have

        # Don't downgrade a terminal payment — webhooks can arrive out of
        # order and re-deliveries can land hours after success.
        if payment.status in ("completed", "failed", "cancelled"):
            return

        payment.status = event.status.value
        if event.reference:
            payment.reference = event.reference
        if event.failure_reason:
            payment.failure_reason = event.failure_reason
        if payment.status in ("completed", "failed", "cancelled"):
            payment.completed_at = datetime.now(UTC)

        run_id = payment.payment_run_id if payment.status == "completed" else None
        await db.commit()

    # ERP sync runs after the DB commit so it sees the latest status.
    if run_id:
        from app.services.payment_erp_sync import dispatch_payment_sync

        await dispatch_payment_sync(run_id, uuid.UUID(str(org.id)))
