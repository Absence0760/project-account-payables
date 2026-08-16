"""Payment endpoints."""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_permission,
    require_roles,
)
from app.api.pagination import PaginationParams, pagination_params
from app.api.permissions import (
    PERM_PAYMENT_EXECUTE,
    PERM_PAYMENT_RUN_APPROVE,
    PERM_PAYMENT_VOID,
)
from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.payment import Payment, PaymentRun, PaymentSchedule
from app.models.user import User
from app.models.vendor import Vendor
from app.models.virtual_card import CardRebate, VirtualCard
from app.schemas.payment import (
    PaymentCreate,
    PaymentListResponse,
    PaymentResponse,
    PaymentRunListResponse,
    PaymentRunResponse,
)
from app.services.audit_access import log_access
from app.services.exception_lifecycle import record_decision
from app.services.international_payments import realized_fx_gain_loss_for_settlement
from app.services.payment_adapters import (
    PaymentAdapter,
    PaymentPayload,
    PaymentStatus,
    UnknownPaymentProviderError,
    get_payment_adapter,
)
from app.services.payment_controls import check_run_segregation
from app.services.payment_runs import (
    PaymentRunItemInput,
    active_run_payments,
    blocked_invoice_ids,
    create_payment_run_for_invoices,
    is_retry_safe,
    net_payable_amount,
    rollup_payment_statuses,
    superseded_payment_ids,
)
from app.services.payment_settlement import (
    SettlementVerification,
    describe_discrepancy,
    settlement_coverage,
    verify_settlement,
)
from app.services.workflow_engine import transition_invoice
from app.tenant import (
    apply_entity_scope,
    get_entity_id,
    get_tenant,
    get_tenant_db,
    get_write_entity_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])

# The invoice statuses a payment may be created against — i.e. an invoice that
# has cleared AP approval and can directly transition to `payment_scheduled`.
# `sent_to_erp` is excluded (mid-flight ERP push must reach `posted_in_erp`
# first); `new`/`pending`/`ready_for_review`/`rejected`/`failed` are pre-approval
# and must never have money scheduled against them. This is the single source of
# truth shared by the queue, the run builder, and the standalone payment record,
# so a payment can't be booked against an unapproved invoice on any path.
PAYABLE_INVOICE_STATUSES = (
    InvoiceStatus.approved.value,
    InvoiceStatus.posted_in_erp.value,
    InvoiceStatus.payment_scheduled.value,
)

# Exception classes that block an invoice from entering a payment run while
# UNRESOLVED (`open`/`escalated`). Every one of them is an `error`-severity
# financial-integrity flag that approval does NOT gate on — nothing in
# `services/review.py` or `workflow_engine.py` reads warning severity — so
# without this gate each could be approved straight past and paid:
#
#   duplicate            — the same invoice paid a second time
#   fraud_flag           — bank-detail swap, rush payment, stat anomaly, an
#                          altered/never-issued cheque from a Positive Pay return
#   line_total_mismatch  — the header `amount` a run pays openly disagrees with
#                          the invoice's own line items (the header is never
#                          silently recomputed from them — see
#                          `docs/line-total-reconciliation.md`), so paying it
#                          would pay a total the lines don't support
#
# Resolving/dismissing the exception is the human sign-off that clears it.
PAYMENT_BLOCKING_EXCEPTION_TYPES = ("duplicate", "fraud_flag", "line_total_mismatch")

# Terminal payment states — a payment in one of these no longer represents a
# LIVE claim on its invoice, so the "one live payment per invoice" idempotency
# invariant (both the app-level guard and the `uq_payments_one_live_per_invoice`
# partial index) excludes them. A void hands the invoice back to `approved` to
# be re-paid; a failed / cancelled attempt must not block a fresh one.
LIVE_PAYMENT_TERMINAL_STATUSES = ("voided", "failed", "cancelled")


def _require_payment_adapter(org: Organization) -> PaymentAdapter:
    """Resolve the org's payment processor, or refuse before anything moves.

    `get_payment_adapter` fails closed on a provider name it has no adapter
    for (see its docstring — the old `mock` fallback reported every payment
    as settled without moving money). Every money-moving entry point resolves
    through here FIRST, so the refusal lands as an actionable 409 with the
    run still in `draft` and no payment dispatched, rather than as a 500 with
    the run stranded `executing`.
    """
    try:
        return get_payment_adapter((org.settings or {}).get("payments") or {})
    except UnknownPaymentProviderError as exc:
        # The provider name is the org's own admin-entered settings value and
        # is bounded by the exception — echoing it is what makes the error
        # actionable ("you typed modern-treasury"). No credential is included.
        raise HTTPException(
            status_code=409,
            detail=(
                f"Payment processor '{exc.provider}' is not a supported provider, so "
                "nothing was dispatched. Fix Settings → Payments before executing."
            ),
        ) from exc


async def _find_live_payment(db: AsyncSession, invoice_id: uuid.UUID) -> Payment | None:
    """Return the oldest non-terminal (LIVE) payment for an invoice, or None.

    Backs the standalone-payment idempotency guard: a live payment is any that
    isn't in LIVE_PAYMENT_TERMINAL_STATUSES. Deterministically returns the
    earliest such row so a double-POST always resolves to the same payment.
    """
    result = await db.execute(
        select(Payment)
        .where(
            Payment.invoice_id == invoice_id,
            Payment.status.notin_(LIVE_PAYMENT_TERMINAL_STATUSES),
        )
        .order_by(Payment.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_scoped_payment(
    db: AsyncSession,
    payment_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    *,
    for_update: bool = False,
) -> Payment:
    """Fetch one `Payment` **within the caller's selected entity**, or 404.

    Multi-entity Phase 2 scopes reads/writes by the `X-Entity-ID` header, and
    the list / queue / summary / counts endpoints all honour it — but every
    by-id detail and mutation route used to resolve the row on `Payment.id`
    alone. Inside one tenant that let a user with subsidiary A selected void,
    release, or read subsidiary B's payment simply by knowing its id: the
    entity selector became advisory on exactly the routes that move money.

    Mirrors `api/positive_pay.py::_get_scoped_file` on the sibling treasury
    router, including its **opaque 404** — an out-of-scope id is
    indistinguishable from one that doesn't exist, so the response can't be
    used to enumerate another entity's payments. `for_update` keeps the
    existing `SELECT ... FOR UPDATE` row locks on the mutating callers (the
    scope predicate is just another WHERE clause; the lock is unchanged).
    """
    query = apply_entity_scope(select(Payment).where(Payment.id == payment_id), Payment, entity_id)
    if for_update:
        query = query.with_for_update()
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return row


async def _get_scoped_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    *,
    for_update: bool = False,
) -> PaymentRun:
    """Fetch one `PaymentRun` within the caller's selected entity, or 404.

    Same rationale (and the same opaque 404) as `_get_scoped_payment`:
    `GET /runs/` is entity-scoped and `POST /runs` stamps the write entity, so
    a run detail / approve / cancel / execute / resume that resolved on
    `PaymentRun.id` alone let one subsidiary's operator CFO-approve and execute
    another subsidiary's run.
    """
    query = apply_entity_scope(
        select(PaymentRun).where(PaymentRun.id == run_id), PaymentRun, entity_id
    )
    if for_update:
        query = query.with_for_update()
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment run not found")
    return row


# ── Individual Payments ──────────────────────────────────────────────


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    pagination: PaginationParams = Depends(pagination_params),
    status_filter: str | None = Query(None, alias="status"),
    method: str | None = None,
    invoice_id: str | None = None,
    search: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    query = (
        select(Payment, Invoice, VirtualCard)
        .outerjoin(Invoice, Payment.invoice_id == Invoice.id)
        .outerjoin(VirtualCard, VirtualCard.payment_id == Payment.id)
    )
    query = apply_entity_scope(query, Payment, entity_id)

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

    # Count — rebuild the filter set against a plain Payment select (the list
    # query's joins would inflate the count via fan-out).
    count_base = apply_entity_scope(select(Payment), Payment, entity_id)
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
    query = query.offset(pagination.offset).limit(pagination.limit)
    result = await db.execute(query)
    rows = result.all()

    return PaymentListResponse(
        items=[PaymentResponse.from_db(p, inv, card) for p, inv, card in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


# NOTE: /queue and /summary MUST be declared before the /{payment_id} route —
# FastAPI matches paths in declaration order, and "queue"/"summary" otherwise
# get parsed as a UUID and fail with a 422 before ever reaching the handler.


@router.get("/queue")
async def payment_queue(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """List approved invoices ready for payment (no completed payment yet)."""
    paid_ids = select(Payment.invoice_id).where(Payment.status == "completed").scalar_subquery()

    # Match the workflow state machine: only statuses that can directly
    # transition to ``payment_scheduled`` belong here. ``sent_to_erp``
    # is excluded — that row is mid-flight in the ERP push and must
    # advance to ``posted_in_erp`` (via the ERP-confirmation webhook)
    # before a payment can be scheduled against it. Including it would
    # let the UI offer "Pay" on a row whose execute call fails the
    # transition with 409, surfacing as a stuck queue row to the
    # operator.
    payable_statuses = PAYABLE_INVOICE_STATUSES

    queue_q = apply_entity_scope(
        select(Invoice, PaymentSchedule)
        .outerjoin(PaymentSchedule, PaymentSchedule.invoice_id == Invoice.id)
        .where(
            Invoice.status.in_(payable_statuses),
            Invoice.id.notin_(paid_ids),
        ),
        Invoice,
        entity_id,
    ).order_by(Invoice.due_date.asc().nulls_last())
    result = await db.execute(queue_q)
    rows = result.all()

    today = date.today()
    items: list[dict] = []
    total_savings = Decimal("0")
    total_amount = Decimal("0")
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

        total_amount += inv.amount or Decimal("0")
        items.append(
            {
                "id": str(inv.id),
                "invoice_number": inv.invoice_number,
                "vendor_name": inv.vendor_name,
                # Money serialises as an exact Decimal STRING, never float() —
                # the frontend coerces with Number() at its arithmetic sites.
                "amount": str(inv.amount),
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
                # discount_percent is a rate, not money — stays a JSON number.
                "discount_percent": float(sched.discount_percent)
                if sched and sched.discount_percent
                else None,
                "discount_amount": str(discount_amount) if discount_amount else None,
            }
        )

    # Both totals are Decimal-accumulated; money serialises as an exact Decimal
    # STRING (never float) — the frontend coerces with Number() where it sums.
    return {
        "items": items,
        "total": len(items),
        "total_amount": str(total_amount),
        "total_savings": str(total_savings),
    }


@router.get("/summary")
async def payment_summary(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """KPIs for the payments page summary bar. Scoped to the selected entity."""
    paid_q = apply_entity_scope(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "completed"),
        Payment,
        entity_id,
    )
    # Money serialises as an exact Decimal STRING, never float().
    total_paid = str((await db.execute(paid_q)).scalar() or Decimal("0"))

    pending_q = apply_entity_scope(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status.in_(["pending", "processing", "submitted"])
        ),
        Payment,
        entity_id,
    )
    total_pending = str((await db.execute(pending_q)).scalar() or Decimal("0"))

    count_q = apply_entity_scope(select(func.count()).select_from(Payment), Payment, entity_id)
    payment_count = (await db.execute(count_q)).scalar() or 0

    # CardRebate is a TENANT-scoped table (it lives in the per-tenant DB, not the
    # control plane — the "control plane" comment here was wrong and made this
    # query run against control_db, where the table doesn't exist, so it silently
    # caught the error and always reported $0 rebates). Query the tenant db,
    # org-wide within the tenant — matching the dashboard KPI.
    try:
        rebate_q = select(func.coalesce(func.sum(CardRebate.amount), 0))
        total_rebates = str((await db.execute(rebate_q)).scalar() or Decimal("0"))
    except Exception:
        await db.rollback()
        total_rebates = "0"

    paid_ids = select(Payment.invoice_id).where(Payment.status == "completed").scalar_subquery()
    # Match the workflow state machine: only statuses that can directly
    # transition to ``payment_scheduled`` belong here. ``sent_to_erp``
    # is excluded — that row is mid-flight in the ERP push and must
    # advance to ``posted_in_erp`` (via the ERP-confirmation webhook)
    # before a payment can be scheduled against it. Including it would
    # let the UI offer "Pay" on a row whose execute call fails the
    # transition with 409, surfacing as a stuck queue row to the
    # operator.
    payable_statuses = PAYABLE_INVOICE_STATUSES
    queue_inner = apply_entity_scope(
        select(Invoice.id).where(
            Invoice.status.in_(payable_statuses),
            Invoice.id.notin_(paid_ids),
        ),
        Invoice,
        entity_id,
    )
    queue_q = select(func.count()).select_from(queue_inner.subquery())
    queue_count = (await db.execute(queue_q)).scalar() or 0

    return {
        "total_paid": total_paid,
        "total_pending": total_pending,
        "payment_count": payment_count,
        "total_rebates": total_rebates,
        "queue_count": queue_count,
    }


@router.get("/counts")
async def payment_status_counts(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Per-status payment tallies for the History-tab filter chips.

    Computed over the WHOLE entity-scoped payment set, not the loaded page, so
    the chip counts (and the "All" count) don't undercount once the history
    list paginates. Mirrors GET /api/vendors/counts. Declared before the
    `/{payment_id}` route so the literal path isn't parsed as a UUID.
    """
    query = apply_entity_scope(
        select(Payment.status, func.count()).select_from(Payment), Payment, entity_id
    ).group_by(Payment.status)
    rows = (await db.execute(query)).all()
    by_status = {str(s): int(n) for s, n in rows}
    return {"total": sum(by_status.values()), "by_status": by_status}


@router.get("/{payment_id}/remittance")
async def get_payment_remittance(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Return a single-page remittance-advice PDF for the payment.

    Currently includes the one invoice the Payment row points at — when
    we group multiple invoices on a single payment in a future schema
    bump, this endpoint will pick up the rest automatically (the line
    item list is built from the row's invoice_id today, but the PDF
    accepts a list)."""
    from app.services.branding import get_brand_context
    from app.services.remittance_pdf import (
        RemittanceContext,
        RemittanceLine,
        render_remittance_pdf,
    )

    payment = await _get_scoped_payment(db, payment_id, entity_id)
    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
    ).scalar_one_or_none()

    company = (org.settings or {}).get("company") or {}

    vendor_address: str | None = None
    if invoice and invoice.vendor_id:
        v_result = await db.execute(select(Vendor.address).where(Vendor.id == invoice.vendor_id))
        vendor_address = v_result.scalar_one_or_none()

    ctx = RemittanceContext(
        payer_name=org.name,
        payer_address=company.get("address") or None,
        vendor_name=invoice.vendor_name if invoice else "Unknown vendor",
        vendor_address=vendor_address or (invoice.remit_to_address if invoice else None),
        payment_date=payment.completed_at or payment.submitted_at or payment.created_at,
        payment_method=payment.method or "ach",
        payment_reference=payment.reference,
        payment_amount=payment.amount,
        currency=invoice.currency if invoice else "USD",
        lines=[
            RemittanceLine(
                invoice_number=(invoice.invoice_number if invoice else str(payment.invoice_id)),
                description=invoice.description if invoice else None,
                amount=payment.amount,
            )
        ],
        brand=get_brand_context(org.settings),
    )
    pdf_bytes = render_remittance_pdf(ctx)

    filename = f"remittance-{payment.reference or str(payment.id)[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    p = await _get_scoped_payment(db, payment_id, entity_id)
    inv = (await db.execute(select(Invoice).where(Invoice.id == p.invoice_id))).scalar_one_or_none()

    # SOX access-control auditing: a payment detail is a regulated money record.
    # Record the view (no banking values enter the audit details). Payment rows
    # carry no organization_id column — the org comes from the authed user (the
    # tenant is already resolved by get_tenant_db, so this can't widen scope).
    await log_access(
        db,
        user=user,
        organization_id=user.organization_id,
        entity_type="payment",
        entity_id=p.id,
        correlation_id=p.correlation_id,
    )
    await db.commit()

    return PaymentResponse.from_db(p, inv)


class VoidPaymentRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


async def _cancel_card_for_void(
    db: AsyncSession,
    *,
    payment: Payment,
    org: Organization,
    user: User,
) -> str | None:
    """Kill the virtual card a voided payment issued. Returns an outcome tag
    for the void's audit row (``None`` when the payment isn't a card payment).

    Voiding a card payment has to reach the provider, not just our books. The
    card is bearer-spendable: left live, the vendor can still redeem it while
    the only payment naming it says ``voided``, and — because it still occupies
    the invoice's live-card slot (``uq_virtual_cards_one_live_per_invoice``
    counts every non-``cancelled`` row) — the next payment run rediscovers it.

    Only an **unspent** card can be cancelled. Once it is ``charged`` /
    ``completed`` the funds have moved and the provider cannot un-spend it, so
    the honest outcome is to record ``card_already_charged`` for AP to chase;
    `card_settlement_block` is what then stops a later run from quietly
    "settling" a new payment against that spent card.

    Provider-FIRST, mirroring ``POST /api/cards/{id}/cancel``: the row is only
    marked cancelled once the provider confirms the close. The fail-safe
    direction is "dead at the provider, maybe stale in the DB" — never the
    reverse. A provider failure is recorded, not raised: an outage must not
    block the accounting void (same posture as the payment rail above).
    """
    if payment.method != "virtual_card":
        return None

    from app.config import settings as app_settings
    from app.services.card_issuance import CARD_SPENT_STATUSES, cancel_card_at_provider

    card = (
        await db.execute(select(VirtualCard).where(VirtualCard.payment_id == payment.id).limit(1))
    ).scalar_one_or_none()
    if card is None:
        return "no_card_linked"
    if card.status == "cancelled":
        return "card_already_cancelled"
    if card.status in CARD_SPENT_STATUSES:
        return "card_already_charged"

    outcome = await cancel_card_at_provider(
        card=card, org_settings=org.settings or {}, app_settings=app_settings
    )
    if outcome != "cancelled":
        return outcome

    prior_status = card.status
    card.status = "cancelled"

    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=card.correlation_id or payment.correlation_id or uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="card.cancelled",
        entity_type="virtual_card",
        entity_id=card.id,
        details={
            "last_four": card.last_four,
            "from": prior_status,
            "to": "cancelled",
            "via": "payment_void",
            "payment_id": str(payment.id),
        },
    )
    return "card_cancelled"


@router.post("/{payment_id}/void")
async def void_payment(
    payment_id: uuid.UUID,
    body: VoidPaymentRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # Defaults map to admin/cfo (unchanged) — see ROLE_DEFAULT_PERMISSIONS.
    user: User = Depends(require_permission(PERM_PAYMENT_VOID)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Void a completed or in-flight payment.

    Adapter dispatch: when the configured processor exposes
    ``void_payment`` we ask it to reverse upstream. If it doesn't (mock /
    legacy rows), we record the local void only — the AP team is
    expected to chase the rail manually. Either way, the invoice flips
    back to ``approved`` so it re-enters the payment queue.
    """
    # Lock the payment row FOR UPDATE and re-check its status inside the
    # transaction. Two concurrent voids would otherwise both pass a
    # non-locking guard, both call the adapter's `void_payment`, and both
    # write a `payment.voided` audit row (double-void). The row lock
    # serializes them: the first transaction flips the status to `voided`
    # and commits; the second blocks on the lock, then re-reads the now-
    # terminal status and 409s before touching the adapter. The Invoice is
    # fetched separately — Postgres can't `FOR UPDATE` the nullable side of
    # an outer join, and we don't need to lock the invoice here.
    payment = await _get_scoped_payment(db, payment_id, entity_id, for_update=True)

    if payment.status in ("voided", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Payment already {payment.status}")
    if payment.status == "failed":
        raise HTTPException(
            status_code=409,
            detail="Cannot void a failed payment (it never settled)",
        )

    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
    ).scalar_one_or_none()

    # Capture the status BEFORE mutating so the audit row records the real
    # prior state (any of completed / submitted / processing / pending).
    previous_status = payment.status

    # Adapter side: best-effort. A processor failure here doesn't block
    # the local void — operators can chase the rail manually, but the
    # accounting books should always reflect intent. That includes an
    # unsupported `settings.payments.provider`: the accounting void still
    # lands, and `provider_not_supported` on the audit row is what tells the
    # operator the rail was never asked (rather than the old behaviour, which
    # called `mock.void_payment` — it returns True unconditionally — and
    # recorded a `voided_upstream` that never happened).
    payment_config = (org.settings or {}).get("payments") or {}
    adapter_outcome: str | None = None
    try:
        adapter = get_payment_adapter(payment_config)
    except UnknownPaymentProviderError:
        adapter = None
        adapter_outcome = "provider_not_supported"
    if adapter is not None and payment.provider_payment_id:
        try:
            void_fn = getattr(adapter, "void_payment", None)
            if callable(void_fn):
                ok = await void_fn(payment.provider_payment_id)
                adapter_outcome = "voided_upstream" if ok else "rejected_by_processor"
            else:
                adapter_outcome = "no_adapter_support"
        except Exception as exc:  # noqa: BLE001
            adapter_outcome = f"adapter_error:{exc.__class__.__name__}"

    # Card side: a voided virtual-card payment must also kill the card, or the
    # void doesn't stop the money — the card stays live and spendable at the
    # provider with no payment behind it, and the next run rediscovers it in the
    # invoice's live-card slot. Best-effort like the payment rail above: a card
    # provider outage records the outcome rather than blocking the accounting
    # void. Only an UNSPENT card can be cancelled (see `_cancel_card_for_void`).
    card_outcome = await _cancel_card_for_void(db, payment=payment, org=org, user=user)

    now = datetime.now(UTC)
    payment.status = "voided"
    payment.failure_reason = f"Voided by {user.full_name}: {body.reason}"
    payment.completed_at = now

    # Reopen the invoice for re-payment if it was scheduled by this row.
    if invoice and invoice.status in (
        InvoiceStatus.payment_scheduled,
        InvoiceStatus.paid,
    ):
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.approved,
            actor_id=user.id,
            action_name="invoice.voided_return_to_approved",
            details={"void_reason": body.reason, "payment_id": str(payment.id)},
        )

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
            "card_outcome": card_outcome,
            "amount": str(payment.amount),
            "previous_status": previous_status or "unknown",
        },
    )
    await db.commit()
    await db.refresh(payment)
    return PaymentResponse.from_db(payment, invoice)


async def _resolve_compliance_hold_exception(
    db: AsyncSession,
    *,
    invoice: Invoice,
    actor_id: uuid.UUID,
    actor_name: str,
    resolution: str,
) -> None:
    """Resolve the open `payment_compliance_hold` exception for an invoice,
    if one exists.

    Delegates to `services/exception_lifecycle.record_decision` — the same
    chokepoint the human queue and the autonomous agents go through — so this
    decision writes the append-only `exception.resolved` row too. Releasing a
    compliance hold is precisely the sign-off that lets held money move, which
    makes it the last decision that should have lived only on the mutable
    `exceptions` row.

    Both callers keep the `resolve` verb (not `dismiss`): a dismissed *payment*
    still means a human cleared the hold, and the queue's status semantics
    predate this. The rationale — `released` vs `dismissed: <reason>` — is what
    distinguishes them, on the immutable row as well as the exception.
    """
    result = await db.execute(
        select(APException).where(
            APException.invoice_id == invoice.id,
            APException.exception_type == "payment_compliance_hold",
            APException.status == "open",
        )
    )
    exc = result.scalar_one_or_none()
    if exc is None:
        return
    await record_decision(
        db,
        exception=exc,
        action="resolve",
        resolution=resolution,
        actor_id=actor_id,
        actor_name=actor_name,
        invoice=invoice,
    )


class DismissComplianceHoldRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


@router.post("/{payment_id}/compliance/release", response_model=PaymentResponse)
async def release_compliance_hold(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # Releasing dispatches the payment to the processor exactly like
    # /execute — same money-moving permission.
    user: User = Depends(require_permission(PERM_PAYMENT_EXECUTE)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Re-run compliance + dispatch for a payment stuck in `pending_compliance`.

    A `hold` verdict (an AML spend-threshold signal, or a `review_required`
    sanctions match — both everyday, non-fraud events; the trailing-12-month
    AML check is explicitly documented as "does NOT refuse — too many false
    positives") used to leave the payment exactly where it landed with no
    way forward. This re-runs `_execute_single_payment`'s full
    compliance-then-adapter path — the SAME gate a fresh /execute would run,
    never a bypass — so a payment that's genuinely still blocked (the hold
    condition hasn't actually changed) stays `pending_compliance` and the
    response reflects that, rather than silently forcing money to move.
    """
    payment = await _get_scoped_payment(db, payment_id, entity_id, for_update=True)
    if payment.status != "pending_compliance":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Can only release a payment stuck 'pending_compliance', not '{payment.status}'"
            ),
        )

    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
    ).scalar_one_or_none()

    # Releasing dispatches to the processor exactly like /execute, so it takes
    # the same pre-flight: an unsupported provider refuses here with the
    # payment still `pending_compliance`, never a 500 mid-dispatch.
    adapter = _require_payment_adapter(org)
    now = datetime.now(UTC)
    await _execute_single_payment(db, payment=payment, org=org, adapter=adapter, user=user, now=now)

    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=payment.correlation_id or uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="payment.compliance_released",
        entity_type="payment",
        entity_id=payment.id,
        details={
            "new_status": payment.status,
            "amount": str(payment.amount),
        },
    )

    # A human just made the release decision — that IS the sign-off. Only
    # resolve the exception if the hold actually cleared; still-held
    # payments keep it open (re-running /release again does nothing new).
    if payment.status != "pending_compliance" and invoice is not None:
        await _resolve_compliance_hold_exception(
            db,
            invoice=invoice,
            actor_id=user.id,
            actor_name=user.full_name,
            resolution="released",
        )

    await db.commit()
    await db.refresh(payment)
    return PaymentResponse.from_db(payment, invoice)


@router.post("/{payment_id}/compliance/dismiss", response_model=PaymentResponse)
async def dismiss_compliance_hold(
    payment_id: uuid.UUID,
    body: DismissComplianceHoldRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # Dismissing moves no money (nothing ever settled) — a treasury decision
    # to give up on this payment, same gate as void.
    user: User = Depends(require_permission(PERM_PAYMENT_VOID)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Give up on a payment stuck in `pending_compliance` — flips it to
    `failed` without ever reaching the processor. AP has reviewed the hold
    (e.g. a genuine sanctions match, or a decision to pay this vendor a
    different way) and decided this payment should not proceed as-is."""
    payment = await _get_scoped_payment(db, payment_id, entity_id, for_update=True)
    if payment.status != "pending_compliance":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Can only dismiss a payment stuck 'pending_compliance', not '{payment.status}'"
            ),
        )

    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    payment.status = "failed"
    payment.failure_reason = f"compliance_dismissed by {user.full_name}: {body.reason}"
    payment.completed_at = now

    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=payment.correlation_id or uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="payment.compliance_dismissed",
        entity_type="payment",
        entity_id=payment.id,
        details={"reason": body.reason, "amount": str(payment.amount)},
    )

    if invoice is not None:
        await _resolve_compliance_hold_exception(
            db,
            invoice=invoice,
            actor_id=user.id,
            actor_name=user.full_name,
            resolution=f"dismissed: {body.reason}",
        )

    await db.commit()
    await db.refresh(payment)
    return PaymentResponse.from_db(payment, invoice)


class AcceptSettlementRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


@router.post("/{payment_id}/settlement/accept", response_model=PaymentResponse)
async def accept_settlement(
    payment_id: uuid.UUID,
    body: AcceptSettlementRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # Declaring a short settlement final closes the payable out — the same
    # money-state authority as executing or voiding a payment.
    user: User = Depends(require_permission(PERM_PAYMENT_EXECUTE)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Accept a short / unverifiable settlement as final and release the invoice.

    This is the release path that makes the ERP-sync hold safe to ship. When
    the rail settles less than AP authorized (or in a currency we never
    authorized), `settlement_coverage` says the invoice is not discharged and
    `payment_erp_sync` leaves it at `payment_scheduled` rather than reporting
    it settled in full. Without an exit that would be a permanent strand — the
    exact defect that forced the first attempt at this hold to be reverted,
    because nothing re-invokes that sweep once a run's payments are terminal.

    There are two legitimate exits and this is the one that keeps the money
    where it landed:

    * **Accept** (here) — the shortfall is agreed with the vendor, or the
      remainder is being handled outside this invoice. The invoice moves to
      `paid` and the `reason` is recorded on the immutable trail.
    * **Void** (`POST /api/payments/{id}/void`) — the settlement is wrong. The
      invoice returns to `approved` to be re-paid correctly. That path already
      accepts a `payment_scheduled` invoice, so it works while held.

    Deliberately does NOT resolve the `fraud_flag` the settlement discrepancy
    raised. Unlike `payment_compliance_hold` — a type only the compliance path
    ever raises — `fraud_flag` is shared with Positive Pay's altered-cheque
    detection, so clearing "the open one" here could silently close an
    unrelated fraud finding. The exception queue stays the separate human
    sign-off, and it writes its own append-only row when someone makes it.
    """
    payment = await _get_scoped_payment(db, payment_id, entity_id, for_update=True)

    # Only a settlement that actually happened can be accepted.
    if payment.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Can only accept the settlement of a 'completed' payment, not '{payment.status}'"
            ),
        )

    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
    ).scalar_one_or_none()

    coverage = settlement_coverage(
        settled_amount=payment.settled_amount,
        settled_currency=payment.settled_currency,
        target_amount=payment.amount,
        target_currency=(invoice.currency if invoice else None),
        source_amount=payment.source_amount,
        source_currency=payment.source_currency,
    )
    # Nothing to accept. Refusing rather than no-op'ing keeps this endpoint
    # from becoming a general-purpose "force the invoice to paid" lever: a
    # fully-covered payment reaches `paid` through the ordinary sync.
    if coverage.completes_invoice:
        raise HTTPException(
            status_code=409,
            detail=(
                "This payment's settlement already covers the invoice; there is nothing to accept."
            ),
        )

    # Nothing to release — checked BEFORE the audit dispatch so a repeat call
    # never even stages a second `payment.settlement_accepted` row. Refusing
    # keeps a retry from letting an operator re-justify the same acceptance
    # indefinitely on the immutable trail; the first call's row is the record.
    if invoice is None or invoice.status.value != "payment_scheduled":
        raise HTTPException(
            status_code=409,
            detail="No held invoice to release for this payment.",
        )

    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=payment.correlation_id or uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="payment.settlement_accepted",
        entity_type="payment",
        entity_id=payment.id,
        details={
            "reason": body.reason,
            "coverage": coverage.state,
            # Money as exact decimal strings, never float. PII-free — amounts,
            # currency codes and the verdict only.
            "shortfall": (None if coverage.shortfall is None else str(coverage.shortfall)),
            "authorized_amount": str(payment.amount),
            "settled_amount": (
                None if payment.settled_amount is None else str(payment.settled_amount)
            ),
            "settled_currency": payment.settled_currency,
        },
    )

    await transition_invoice(
        db,
        invoice,
        InvoiceStatus.paid,
        actor_id=user.id,
        action_name="invoice.paid_via_settlement_acceptance",
        details={"payment_id": str(payment.id), "coverage": coverage.state},
    )

    await db.commit()
    await db.refresh(payment)
    return PaymentResponse.from_db(payment, invoice)


@router.post("", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment(
    body: PaymentCreate,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # Recording a standalone payment moves money exactly like executing a run,
    # so it gates on the same splittable SoD permission — not bare roles. An org
    # that strips payment.execute from a custom role must not retain a back door
    # to book money here. (System roles resolve identically via the default map.)
    user: User = Depends(require_permission(PERM_PAYMENT_EXECUTE)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    # Verify invoice exists and has cleared approval. Recording a payment
    # against a pre-approval invoice (new/pending/ready_for_review/rejected/
    # failed) would book money against something nobody signed off on.
    #
    # Lock the invoice FOR UPDATE so two concurrent / double-clicked POSTs for
    # the same invoice serialize here: the first books the payment and commits,
    # the second blocks on this lock, then re-reads and returns the payment
    # already booked instead of creating a duplicate full-amount one. Without
    # the lock the idempotency check below is a non-atomic read→check→write that
    # a concurrent POST races through, booking a second payment (a real double-
    # pay with no audit distinction). The `uq_payments_one_live_per_invoice`
    # partial index (migration 0074) is the DB-level backstop for any path the
    # row lock can't cover (e.g. an overlapping payment run).
    #
    # The lookup is entity-scoped like every other by-id money route on this
    # router: with subsidiary A selected, an invoice belonging to subsidiary B
    # is the same opaque 404 as one that doesn't exist — booking a payment
    # against it would put A's operator on B's money.
    inv_result = await db.execute(
        apply_entity_scope(
            select(Invoice).where(Invoice.id == uuid.UUID(body.invoice_id)),
            Invoice,
            entity_id,
        ).with_for_update()
    )
    invoice = inv_result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status not in PAYABLE_INVOICE_STATUSES:
        raise HTTPException(status_code=409, detail="Invoice is not approved for payment")

    # The payment amount is the invoice amount NET OF APPLIED CREDIT MEMOS —
    # never a caller-supplied value. Trusting `body.amount` let an actor book a
    # $99,999 payment against a $500 approved invoice, so the figure is bound
    # server-side; but binding it to the raw `invoice.amount` made this path
    # ignore credit memos entirely, paying the vendor the full pre-credit
    # amount AND 422ing the correct net figure if a caller tried to submit it.
    # `net_payable_amount` is the same helper the run builder uses, so the two
    # money paths can't disagree about what an invoice is worth.
    net_amount = await net_payable_amount(db, invoice)
    if net_amount <= 0:
        raise HTTPException(
            status_code=409,
            detail="Invoice is fully covered by applied credit memos — nothing to pay",
        )
    if body.amount is not None and Decimal(str(body.amount)) != net_amount:
        raise HTTPException(
            status_code=422,
            detail=(
                "Payment amount must equal the approved invoice amount net of applied credit memos"
            ),
        )

    # CFO sign-off gate — the same `payments.cfo_approval_above` threshold the
    # run-based path enforces (issue #129). A standalone payment has no
    # separate /execute step to gate the way a run does (requires_cfo_approval
    # lives on PaymentRun, not Payment), so an above-threshold amount must
    # clear CFO sign-off at CREATION time instead: only a CFO may book one
    # directly here; anyone else routes an above-threshold payment through a
    # payment run, which carries the full requires_cfo_approval / /approve
    # workflow. This is a structural close of the gap, not a reaction to a
    # live exploit — see the issue's severity note. Mirrors
    # create_payment_run's `requires_cfo` computation exactly (same threshold
    # setting, same fail-closed handling of a corrupted/unparseable value).
    pmt_cfg = (org.settings or {}).get("payments") or {}
    cfo_threshold_raw = pmt_cfg.get("cfo_approval_above")
    requires_cfo = False
    if cfo_threshold_raw is not None:
        try:
            cfo_threshold = Decimal(str(cfo_threshold_raw))
        except (ValueError, ArithmeticError):
            logger.error(
                "payments.cfo_approval_above is unparseable (%r) for org %s; "
                "requiring CFO sign-off on this standalone payment (fail-closed)",
                cfo_threshold_raw,
                org.id,
            )
            requires_cfo = True
        else:
            # Strict `>` matches the setting name; a threshold of 0/negative
            # means "no gate" — same semantics as create_payment_run. The
            # comparison is against the NET amount, i.e. the money that
            # actually moves, exactly as create_payment_run compares its
            # credit-netted total.
            if cfo_threshold > 0 and net_amount > cfo_threshold:
                requires_cfo = True

    if requires_cfo:
        has_cfo = any(r.name == ROLE_CFO for r in (user.roles or ()))
        if not has_cfo:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This payment requires CFO sign-off — book it through a payment "
                    "run for CFO approval, or have a CFO create it directly"
                ),
            )

    # Idempotency guard: at most one LIVE payment per invoice. A retried or
    # double-clicked POST must not book a second payment — return the existing
    # live one instead of creating a duplicate. Terminal states don't count as
    # live (see LIVE_PAYMENT_TERMINAL_STATUSES).
    existing = await _find_live_payment(db, invoice.id)
    if existing is not None:
        return PaymentResponse.from_db(existing, invoice)

    payment = Payment(
        invoice_id=invoice.id,
        # Payment follows the invoice's entity (multi-entity Phase 2).
        entity_id=invoice.entity_id,
        amount=net_amount,
        method=body.method.value if body.method else None,
        reference=body.reference,
        payment_run_id=uuid.UUID(body.payment_run_id) if body.payment_run_id else None,
        correlation_id=uuid.uuid4(),
    )
    # Insert inside a savepoint so the DB-level unique index (the backstop for a
    # race the row lock can't serialize — e.g. an overlapping run booking a live
    # payment for the same invoice between our check and flush) surfaces as an
    # IntegrityError we recover from, returning the winning payment rather than
    # 500ing.
    try:
        async with db.begin_nested():
            db.add(payment)
            await db.flush()
    except IntegrityError:
        existing = await _find_live_payment(db, invoice.id)
        if existing is not None:
            return PaymentResponse.from_db(existing, invoice)
        raise

    # Append-only audit trail for the money-booking event. Every sibling money
    # handler (void_payment, create_payment_run, execute_payment_run) writes an
    # audit row; this standalone path was the only one that didn't. PII-free:
    # ids, the Decimal amount as a string, method, and reference only — no
    # bank/routing numbers, no PAN.
    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=payment.correlation_id or uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="payment.created",
        entity_type="payment",
        entity_id=payment.id,
        details={
            "invoice_id": str(invoice.id),
            "amount": str(payment.amount),
            "method": payment.method,
            "reference": payment.reference,
            "payment_run_id": str(payment.payment_run_id) if payment.payment_run_id else None,
        },
    )

    await db.refresh(payment)
    return PaymentResponse.from_db(payment, invoice)


# ── Payment Runs ─────────────────────────────────────────────────────


@router.get("/runs/", response_model=PaymentRunListResponse)
async def list_payment_runs(
    pagination: PaginationParams = Depends(pagination_params),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    query = apply_entity_scope(select(PaymentRun), PaymentRun, entity_id)

    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(PaymentRun.status.in_(statuses))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(PaymentRun.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.limit)
    result = await db.execute(query)
    runs = result.scalars().all()

    # Per-run outcome tallies in ONE grouped query, not a count per run (this
    # was an N+1). The rollup is what makes a `partial` row actionable in the
    # list — "3 of 12 failed" instead of a bare status word.
    run_ids = [run.id for run in runs]
    per_run: dict[uuid.UUID, list[str | None]] = {rid: [] for rid in run_ids}
    if run_ids:
        # Exclude any attempt a later retry on these runs superseded, matching
        # `services/payment_runs.active_run_payments` (the run detail and the
        # dispatcher's own rollup both go through it). Expressed as a subquery
        # rather than in Python so this stays the single grouped query it was
        # built to be, not an N+1.
        superseded_q = select(Payment.retry_of_payment_id).where(
            Payment.payment_run_id.in_(run_ids),
            Payment.retry_of_payment_id.isnot(None),
        )
        status_rows = await db.execute(
            select(Payment.payment_run_id, Payment.status, func.count())
            .where(
                Payment.payment_run_id.in_(run_ids),
                Payment.id.notin_(superseded_q),
            )
            .group_by(Payment.payment_run_id, Payment.status)
        )
        for pay_run_id, pay_status, n in status_rows.all():
            per_run[pay_run_id].extend([pay_status] * int(n))

    items = []
    for run in runs:
        rollup = rollup_payment_statuses(per_run.get(run.id, ()))
        items.append(
            PaymentRunResponse.from_db(
                run,
                rollup.total,
                completed=rollup.completed,
                failed=rollup.failed,
                in_flight=rollup.in_flight,
                pending=rollup.pending,
            )
        )

    return PaymentRunListResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
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
    org: Organization = Depends(get_tenant),
    # SoD-splittable: creating (approving) a payment run is the gate before
    # execution. Defaults map to admin/ap_manager/cfo (unchanged); a custom role
    # can be granted run-approval WITHOUT execution, and vice versa.
    user: User = Depends(require_permission(PERM_PAYMENT_RUN_APPROVE)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    """Create a payment run from selected invoices.

    The validation + creation gates (payable-status, the financial-integrity
    exception block, credit-memo netting, the CFO-approval threshold, and the
    `uq_payments_one_live_per_invoice` idempotency backstop) live in
    `services.payment_runs.create_payment_run_for_invoices` — shared verbatim
    with the AI Cash-Flow Copilot's draft-run enact route
    (`POST /api/cash-flow/plans/{plan_id}/draft-run`) so the two can never
    diverge on what counts as a legitimate run.
    """
    items = [
        PaymentRunItemInput(invoice_id=uuid.UUID(item.invoice_id), method=item.method)
        for item in body.items
    ]
    result = await create_payment_run_for_invoices(
        db, org=org, org_id=org_id, entity_id=entity_id, user=user, items=items
    )
    await db.commit()

    run = result.run
    return {
        "id": str(run.id),
        "status": run.status,
        # Money serialises as an exact Decimal STRING, never float().
        "total_amount": str(result.total_amount),
        "payment_count": result.payment_count,
        "requires_cfo_approval": run.requires_cfo_approval,
        "message": (
            f"Payment run created with {result.payment_count} payments totaling "
            f"${result.total_amount:,.2f}"
            + (" (CFO approval required)" if run.requires_cfo_approval else "")
        ),
    }


@router.get("/runs/{run_id}")
async def get_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Get a payment run with its individual payments.

    Each payment carries its own `failure_reason` (plus `provider` and the two
    lifecycle timestamps) and the run carries the per-outcome rollup, so a
    `partial` run explains itself: which payments failed, and why. Before this,
    the counts existed only in the transient response of the `/execute` call
    that produced them and `failure_reason` never left the database — a reload
    lost both, and the operator's only recourse was the server log.
    """
    run = await _get_scoped_run(db, run_id, entity_id)

    # Get payments in this run with invoice details
    pay_result = await db.execute(
        select(Payment, Invoice)
        .outerjoin(Invoice, Payment.invoice_id == Invoice.id)
        .where(Payment.payment_run_id == run_id)
        .order_by(Payment.created_at.asc())
    )
    rows = pay_result.all()
    payments = [
        {
            "id": str(p.id),
            "invoice_id": str(p.invoice_id),
            "invoice_number": inv.invoice_number if inv else None,
            "vendor_name": inv.vendor_name if inv else None,
            # Money serialises as an exact Decimal STRING, never float().
            "amount": str(p.amount),
            "method": p.method,
            "status": p.status,
            "reference": p.reference,
            "provider": p.provider,
            "failure_reason": p.failure_reason,
            "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
            "completed_at": p.completed_at.isoformat() if p.completed_at else None,
        }
        for p, inv in rows
    ]
    # The rollup counts the LATEST attempt per invoice — a superseded retry
    # attempt is still listed above (an operator has to be able to see that an
    # invoice took two goes) but must not be counted twice, or a fully
    # recovered run would read `partial` forever.
    active = active_run_payments(p for p, _ in rows)
    rollup = rollup_payment_statuses(p.status for p in active)

    return {
        "id": str(run.id),
        "status": run.status,
        # Money serialises as an exact Decimal STRING, never float().
        "total_amount": str(run.total_amount) if run.total_amount else "0",
        "initiated_by": str(run.initiated_by) if run.initiated_by else None,
        "executed_at": run.executed_at.isoformat() if run.executed_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else "",
        "requires_cfo_approval": run.requires_cfo_approval,
        "cfo_approved_by": str(run.cfo_approved_by) if run.cfo_approved_by else None,
        "cfo_approved_at": run.cfo_approved_at.isoformat() if run.cfo_approved_at else None,
        "payment_count": rollup.total,
        "payments_completed": rollup.completed,
        "payments_failed": rollup.failed,
        "payments_in_flight": rollup.in_flight,
        "payments_pending": rollup.pending,
        # Failures `/runs/{id}/retry-failed` will ACTUALLY re-attempt — what
        # the retry button gates on. A failure we can't prove never reached the
        # processor (`is_retry_safe`) is excluded: the endpoint would only skip
        # it as `needs_reconciliation`, and offering a button that can't act is
        # how an operator ends up hunting for a second way to force the payment.
        "retryable_failures": sum(
            1 for p in active if p.status in RETRYABLE_PAYMENT_STATUSES and is_retry_safe(p)
        ),
        "payments": payments,
    }


@router.post("/runs/{run_id}/approve")
async def approve_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """CFO sign-off on a draft run. Only valid from `draft` AND
    `requires_cfo_approval=True`. After this lands, /execute will accept
    the run from any actor with the standard payments role set."""
    # Row-lock the run: two concurrent CFO approvals both read
    # cfo_approved_at=None, both pass the guards, and both commit — last writer
    # wins cfo_approved_by and a duplicate `payment_run.cfo_approved` audit row
    # lands, breaking non-repudiation of the money-control gate. The lock
    # serialises them so the second sees the first's commit and 409s.
    run = await _get_scoped_run(db, run_id, entity_id, for_update=True)
    if run.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"Can only approve 'draft' runs, not '{run.status}'",
        )
    if not run.requires_cfo_approval:
        raise HTTPException(
            status_code=409,
            detail="This run does not require CFO approval",
        )
    if run.cfo_approved_at is not None:
        raise HTTPException(status_code=409, detail="Run is already CFO-approved")
    # Maker-checker: the user who created the run cannot also sign it off — a
    # self-approval defeats the entire purpose of the CFO gate.
    check_run_segregation(
        run.initiated_by,
        user.id,
        (org.settings or {}).get("payments"),
        action="approve",
    )

    now = datetime.now(UTC)
    run.cfo_approved_by = user.id
    run.cfo_approved_at = now

    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="payment_run.cfo_approved",
        entity_type="payment_run",
        entity_id=run.id,
        details={"total_amount": str(run.total_amount or Decimal("0"))},
    )
    await db.commit()
    return {
        "id": str(run.id),
        "status": run.status,
        "cfo_approved_by": str(run.cfo_approved_by),
        "cfo_approved_at": run.cfo_approved_at.isoformat(),
        "message": "Run approved by CFO",
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Cancel a draft run before it executes. Only valid from `draft`;
    flips the run to `cancelled` and removes its child payment rows so
    the invoices return to the queue. Audit-logged."""
    # Row-lock the run, exactly like /approve, /execute and /resume. This one
    # DELETES the child Payment rows, so an unlocked read is the worst version
    # of the race: /execute takes the lock, flips the run to `executing`,
    # commits (releasing it) and starts handing payments to the processor —
    # while a /cancel that read `draft` before any of that proceeds to delete
    # the very rows being dispatched. Real money then moves against rows that
    # no longer exist, under a run that reads `cancelled`. With the lock the
    # canceller blocks until /execute commits, re-reads `executing`, and 409s
    # before deleting anything.
    run = await _get_scoped_run(db, run_id, entity_id, for_update=True)
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
            "total_amount": str(run.total_amount or Decimal("0")),
        },
    )
    await db.commit()

    return {
        "id": str(run.id),
        "status": run.status,
        "released_invoices": len(invoice_ids),
        "message": (f"Draft run cancelled; {len(invoice_ids)} invoice(s) returned to the queue."),
    }


async def _open_compliance_hold_exception(
    db: AsyncSession,
    *,
    payment: Payment,
    invoice: Invoice | None,
    org: Organization,
) -> None:
    """Surface a `pending_compliance` payment in the Exceptions queue.

    `check_payment_compliance`'s own docstring promises a hold "opens an
    exception for AP review" — until this, none of the four call sites that
    set `payment.status = "pending_compliance"` actually did, so a held
    payment was invisible everywhere except its own `failure_reason` field.
    Dedupes on `(invoice_id, "payment_compliance_hold", "open")`: a payment
    without a screenable vendor can be re-dispatched (e.g. by /resume) and
    hit the same hold repeatedly, and `uq_payments_one_live_per_invoice`
    means at most one live payment exists per invoice at a time, so an
    invoice-scoped dedupe is equivalent to a payment-scoped one.
    """
    if invoice is None:
        return
    from app.services.exception_service import create_exception

    existing = await db.execute(
        select(APException.id).where(
            APException.invoice_id == invoice.id,
            APException.exception_type == "payment_compliance_hold",
            APException.status == "open",
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    await create_exception(
        db,
        exception_type="payment_compliance_hold",
        description=payment.failure_reason,
        organization_id=org.id,
        severity="error",
        invoice=invoice,
    )


async def _open_settlement_mismatch_exception(
    db: AsyncSession,
    *,
    payment: Payment,
    invoice: Invoice | None,
    org: Organization,
    verification: SettlementVerification,
) -> None:
    """Raise a payment-blocking `fraud_flag` for a settlement that didn't
    reconcile against what AP authorized.

    `fraud_flag` deliberately, not a new taxonomy entry: it is exactly what
    `api/positive_pay.py` raises for an ALTERED cheque — a payment instrument
    presented at an amount we never wrote — and this is the electronic
    equivalent, caught days earlier on rails where no cheque exists. It is
    also in `PAYMENT_BLOCKING_EXCEPTION_TYPES`, so until a human resolves it
    the invoice can't be swept into another payment run (which matters the
    moment this payment is voided and the invoice returns to `approved`).

    Dedupes on `(invoice_id, fraud_flag, open|escalated)` — the same rule
    Positive Pay's own return processing uses. One open fraud flag on an
    invoice is the signal; piling on a second adds noise, not information.
    The description is PII-free (amounts, currency codes and the verdict —
    the row already carries the invoice FK).
    """
    if invoice is None:
        # A payment whose invoice row is gone still gets the audit row above;
        # there is no queue entry to attach the flag to.
        return
    from app.services.exception_service import create_exception

    already = (
        await db.execute(
            select(func.count()).where(
                APException.invoice_id == invoice.id,
                APException.exception_type == "fraud_flag",
                APException.status.in_(["open", "escalated"]),
            )
        )
    ).scalar() or 0
    if already > 0:
        return

    await create_exception(
        db,
        exception_type="fraud_flag",
        description=describe_discrepancy(verification),
        organization_id=org.id,
        severity="error",
        invoice=invoice,
    )


async def _capture_discount_offers(
    db: AsyncSession,
    *,
    org: Organization,
    payment: Payment,
    actor_id: uuid.UUID | None,
    now: datetime,
    invoice: Invoice | None = None,
) -> None:
    """Recognize `payment` settling its invoice at a discounted payoff and
    capture any matching `accepted` `DiscountOffer` — the wiring
    `discount_offers.mark_captured` was missing (issue #280): without a
    caller, `captured_amount`/`captured_at` never got set and the
    captured-savings KPI always read 0 even when discounts were genuinely
    accepted and paid at the discounted amount.

    Called from every path a `Payment` reaches `completed` — the synchronous
    adapter/card leg (`_execute_single_payment`) and the async webhook-driven
    completion (`payment_webhook`) — so a discount is recognized whether the
    rail confirms instantly or days later. Both callers already hold the
    `Invoice` and pass it in; `invoice=None` falls back to resolving it from
    `payment.invoice_id` for any future caller that doesn't. No invoice found
    is a no-op (nothing to match against); a payment amount that doesn't
    match a discounted payoff exactly is also a no-op (see
    `discount_capture.capture_offers_for_settled_payment`).

    NOT called when the settlement verifier flagged a discrepancy — the
    payoff match runs against OUR authorized amount, which a divergent
    settlement has just contradicted. See the call site in `payment_webhook`.

    Best-effort, like the vendor card-notify email below: this labels a
    payment that already, definitely settled with a bookkeeping fact
    (realized discount savings) — it must never be the reason a payment
    that DID move money fails to record that it moved (or, on the webhook
    path, the reason a webhook delivery 5xxs and gets needlessly retried).
    The invoice lookup lives INSIDE the try for exactly that reason. A
    failure here is logged (class only — no invoice/vendor PII) and
    swallowed rather than propagated, mirroring `notify_vendor_of_card`'s
    own safety net.
    """
    try:
        if invoice is None:
            inv_result = await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
            invoice = inv_result.scalar_one_or_none()
        if invoice is None:
            return

        from app.services.discount_capture import capture_offers_for_settled_payment

        captured = await capture_offers_for_settled_payment(
            db,
            invoice_id=invoice.id,
            payment_amount=payment.amount,
            invoice_currency=invoice.currency,
            now=now,
        )
        if not captured:
            return

        from app.services.audit_dispatch import dispatch_audit

        for offer in captured:
            await dispatch_audit(
                db,
                correlation_id=payment.correlation_id or invoice.id,
                organization_id=org.id,
                actor_id=actor_id,
                action="discount_offer.captured",
                entity_type="discount_offer",
                entity_id=offer.id,
                details={
                    "invoice_id": str(invoice.id),
                    "payment_id": str(payment.id),
                    "captured_amount": str(offer.captured_amount),
                },
            )
    except Exception as exc:  # noqa: BLE001
        # Log the exception CLASS only, never the message (PII-out-of-logs
        # invariant — mirrors payment_erp_sync.py's own discipline). Never
        # `payment.invoice_id` either — best-effort but still no PII risk.
        logger.warning(
            "discount-offer capture failed for payment=%s: %s; payment settlement unaffected",
            payment.id,
            exc.__class__.__name__,
        )


async def _execute_single_payment(
    db: AsyncSession,
    *,
    payment: Payment,
    org: Organization,
    adapter,
    user: User,
    now: datetime,
) -> None:
    """Dispatch ONE payment to its processor (or the card adapter), mutating
    it to a terminal or in-flight status in place.

    Extracted out of the `execute_payment_run` loop so each payment can be
    committed durably right after this call returns (see the caller) — a
    problem with payment N (including this raising) must only ever affect
    payment N, never roll back payments the loop already committed earlier.
    """
    # Resolve invoice + vendor for the payload
    inv_result = await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
    invoice = inv_result.scalar_one_or_none()
    vendor_bank: dict | None = None
    if invoice and invoice.vendor_id:
        v_result = await db.execute(
            select(Vendor.bank_details).where(Vendor.id == invoice.vendor_id)
        )
        vendor_bank = v_result.scalar_one_or_none()

    # Virtual-card method: skip the payment adapter and mint a card
    # via the card adapter instead. The Payment row still settles
    # locally — the rebate flow runs off VirtualCard webhooks, not
    # the payment status.
    if payment.method == "virtual_card" and invoice is not None:
        from app.config import settings as app_settings
        from app.services.card_issuance import (
            card_settlement_block,
            find_live_card_for_invoice,
            issue_card_for_invoice,
            notify_vendor_of_card,
            persist_card,
        )

        # Issuing a virtual card moves money just like an ACH/wire, so the
        # same compliance gate applies: a blocked / sanctioned vendor must
        # not receive a card. Refuse outright; a review-hold leaves the
        # payment in pending_compliance for AP (no card minted).
        from app.services.compliance import check_payment_compliance

        v_card = None
        if invoice.vendor_id:
            v_card = (
                await db.execute(select(Vendor).where(Vendor.id == invoice.vendor_id))
            ).scalar_one_or_none()
        if v_card is None:
            # No screenable vendor (invoice never matched a Vendor, or the
            # row was deleted). We cannot run sanctions/KYC against a payee
            # we don't have — and an AI-extracted / email-intake invoice can
            # reach here with vendor_id NULL. Minting a card anyway would put
            # funds on a card for an unscreened name, defeating the gate.
            # Fail-safe: hold for AP to attach + verify a vendor (mirrors the
            # ACH/wire leg); never mint a card unscreened.
            payment.status = "pending_compliance"
            payment.failure_reason = "compliance_hold: no screenable vendor on invoice"
            await _open_compliance_hold_exception(db, payment=payment, invoice=invoice, org=org)
            return
        card_decision = await check_payment_compliance(
            db,
            vendor=v_card,
            payment_amount=payment.amount,
            payment_method=payment.method,
            org_settings=org.settings or {},
            organization_id=org.id,
            correlation_id=payment.correlation_id,
        )
        if card_decision.verdict == "refuse":
            payment.status = "failed"
            payment.failure_reason = "compliance_refusal: " + "; ".join(card_decision.reasons)
            payment.completed_at = now
            return
        if card_decision.verdict == "hold":
            payment.status = "pending_compliance"
            payment.failure_reason = "compliance_hold: " + "; ".join(card_decision.reasons)
            await _open_compliance_hold_exception(db, payment=payment, invoice=invoice, org=org)
            return

        # Idempotency pre-check, mirroring the batch `/api/cards/generate` leg:
        # an invoice can already hold a LIVE card (minted there, or by a
        # concurrent payment run). `uq_virtual_cards_one_live_per_invoice`
        # would reject a second one anyway — but only AFTER the provider had
        # already minted a real, separately-spendable card, orphaning it. Skip
        # the provider entirely and converge on the card that already pays this
        # invoice.
        card = await find_live_card_for_invoice(db, invoice.id)
        minted = False
        if card is None:
            issue = await issue_card_for_invoice(
                db=db,
                invoice=invoice,
                organization_id=org.id,
                org_settings=org.settings or {},
                app_settings=app_settings,
                payment_id=payment.id,
                amount=payment.amount,
            )
            if not issue.success or issue.card is None:
                payment.status = "failed"
                payment.failure_reason = issue.failure_reason or "card_issuance_failed"
                payment.completed_at = now
                return
            # Savepoint-guarded flush (we need card.id for the reveal-token
            # row). A racer that committed the invoice's live card between the
            # pre-check and here trips the unique index; containing that in a
            # savepoint keeps THIS transaction usable, so the dispatch loop can
            # still write its audit row and commit the payment.
            if await persist_card(db, issue.card):
                card = issue.card
                minted = True
            else:
                # Lost the race. Both racers derive the SAME provider
                # idempotency key from the invoice, so the winner's row is the
                # same provider card ours would have been — adopt it.
                card = await find_live_card_for_invoice(db, invoice.id)

        if card is None:
            # The live-card slot was contended and is now empty (the winner
            # cancelled its card between our flush and this re-read). Don't
            # guess — surface it for AP rather than silently retry the provider.
            payment.status = "failed"
            payment.failure_reason = "card_issuance_conflict"
            payment.completed_at = now
            return

        if not minted:
            # Converging marks this payment `completed` — money moved. Only do
            # that against a card that can actually be what moved it (unspent,
            # and big enough). See `card_settlement_block`.
            block = card_settlement_block(card, payment.amount)
            if block is not None:
                payment.status = "failed"
                payment.failure_reason = block
                payment.completed_at = now
                return
            # Link the card to THIS payment when nothing else owns it (a card
            # from `POST /api/cards/generate` carries no payment_id). The
            # payments list resolves a row's card via
            # `VirtualCard.payment_id == Payment.id`, so without this the UI
            # shows no card on a converged payment whose reference says
            # `CARD-…`. Never re-point a card that already names another
            # payment — that payment is live and the link is its badge.
            if card.payment_id is None:
                card.payment_id = payment.id

        payment.status = "completed"
        payment.provider = card.card_provider
        payment.completed_at = now
        payment.submitted_at = now
        payment.reference = f"CARD-{card.card_provider.upper()}-{card.last_four or '????'}"
        await _capture_discount_offers(
            db, org=org, invoice=invoice, payment=payment, actor_id=user.id, now=now
        )
        if invoice.status.value in (
            "approved",
            "sent_to_erp",
            "posted_in_erp",
        ):
            await transition_invoice(
                db,
                invoice,
                InvoiceStatus.payment_scheduled,
                actor_id=user.id,
                action_name="invoice.card_payment_scheduled",
                details={"payment_id": str(payment.id)},
            )

        # SOX trail for the card itself. `card.generated` matches the batch
        # endpoint so a card-lifecycle query (entity_type=virtual_card,
        # entity_id=card.id) shows a creation event on BOTH mint paths, not just
        # later webhook rows; `card.reused` records that this payment settled
        # against a card it did not mint, which an auditor reconciling the run
        # would otherwise have to infer from timestamps. Both PII-free — ids,
        # last four, and the exact amount as a string; never the PAN.
        from app.services.audit_dispatch import dispatch_audit

        await dispatch_audit(
            db,
            correlation_id=payment.correlation_id or invoice.correlation_id or uuid.uuid4(),
            organization_id=org.id,
            actor_id=user.id,
            action="card.generated" if minted else "card.reused",
            entity_type="virtual_card",
            entity_id=card.id,
            details={
                "invoice_id": str(invoice.id),
                "payment_id": str(payment.id),
                "last_four": card.last_four,
                "amount": str(payment.amount),
            },
        )

        if not minted:
            # The vendor was already emailed a reveal link when this card was
            # minted; a second one would mint a second single-use token for the
            # same card and confuse the supplier. Notify on a fresh mint only.
            return

        # Best-effort vendor notification — single-use reveal
        # link emailed to the vendor's contact address.
        try:
            await notify_vendor_of_card(
                db,
                card=card,
                invoice=invoice,
                org_name=org.name,
                org_slug=org.slug,
                public_url_template=app_settings.tenant_url_template,
            )
        except Exception:  # noqa: BLE001
            # `notify_vendor_of_card` already swallows known
            # failures; this catch is the safety net for the
            # "the email path raised before the function could
            # log" edge case. Card issuance itself is committed.
            pass
        return

    # International leg: if the invoice's currency isn't the org's
    # home currency (or the payment was already prepared via
    # prepare_international_payment), lock an FX rate before the
    # adapter call and persist the source-side outflow + rate on
    # the row. The corridor lookup also decides whether the row
    # needs to flip to `sepa` / `international_wire`.
    invoice_currency = (invoice.currency if invoice else "USD").upper()
    org_home_currency = (
        ((org.settings or {}).get("payments") or {}).get("home_currency") or "USD"
    ).upper()
    has_intl_bank_fields = bool(
        vendor_bank and (vendor_bank.get("iban") or vendor_bank.get("swift_bic"))
    )
    if (
        invoice is not None
        and (
            invoice_currency != org_home_currency
            or payment.method in {"sepa", "international_wire", "international_ach"}
            or has_intl_bank_fields
        )
        and payment.fx_rate is None  # not already prepared
    ):
        from app.services.fx_adapters import get_fx_adapter
        from app.services.international_payments import (
            InternationalPaymentError,
            prepare_international_payment,
        )

        v_for_corridor = SimpleNamespace(
            bank_details=vendor_bank or {},
            address_country=getattr(invoice, "vendor_country", None),
        )
        fx_cfg = (org.settings or {}).get("fx") or {}
        fx_adapter = get_fx_adapter(fx_cfg)
        try:
            prepared = await prepare_international_payment(
                invoice=invoice,
                vendor=v_for_corridor,
                org_home_currency=org_home_currency,
                fx_adapter=fx_adapter,
                requested_method=payment.method,
            )
        except InternationalPaymentError as exc:
            payment.status = "failed"
            payment.failure_reason = f"international_payment_error: {exc}"
            payment.completed_at = now
            return

        payment.method = prepared.corridor.method
        payment.source_currency = prepared.payment.source_currency
        payment.source_amount = prepared.payment.source_amount
        payment.fx_rate = prepared.payment.fx_rate
        payment.fx_locked_at = prepared.payment.fx_locked_at
        payment.corridor = prepared.payment.corridor
        payment.target_country = prepared.payment.target_country

    # Compliance gate: run sanctions / KYC / AML checks against the
    # vendor + the resolved corridor BEFORE the adapter call. This runs
    # for EVERY rail (domestic ACH / wire / check as well as the
    # international leg above) — the sticky `payments_blocked` block and
    # a sanctions `match` must refuse a payment no matter the corridor,
    # so this gate must NOT be nested under the international-leg `if`
    # (a blocked vendor paid via domestic ACH would otherwise slip
    # through unscreened). A refusal fails the payment outright; a hold
    # leaves it in pending_compliance for AP review.
    if invoice is not None:
        from app.services.compliance import check_payment_compliance

        v_full = None
        if invoice.vendor_id:
            v_result = await db.execute(select(Vendor).where(Vendor.id == invoice.vendor_id))
            v_full = v_result.scalar_one_or_none()
        if v_full is None:
            # No screenable vendor (invoice never matched a Vendor, or the
            # row was deleted). We CANNOT run sanctions/KYC against a payee
            # we don't have — and an AI-extracted / email-intake invoice can
            # reach here with vendor_id NULL. Paying anyway would route money
            # to an unscreened name, defeating the gate. Fail-safe: hold for
            # AP to attach + verify a vendor, never pay unscreened.
            payment.status = "pending_compliance"
            payment.failure_reason = "compliance_hold: no screenable vendor on invoice"
            await _open_compliance_hold_exception(db, payment=payment, invoice=invoice, org=org)
            return
        decision = await check_payment_compliance(
            db,
            vendor=v_full,
            payment_amount=payment.amount,
            payment_method=payment.method,
            org_settings=org.settings or {},
            organization_id=org.id,
            correlation_id=payment.correlation_id,
        )
        if decision.verdict == "refuse":
            payment.status = "failed"
            payment.failure_reason = "compliance_refusal: " + "; ".join(decision.reasons)
            payment.completed_at = now
            return
        if decision.verdict == "hold":
            payment.status = "pending_compliance"
            payment.failure_reason = "compliance_hold: " + "; ".join(decision.reasons)
            # Hold doesn't flip the invoice — money hasn't moved.
            await _open_compliance_hold_exception(db, payment=payment, invoice=invoice, org=org)
            return

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
        source_currency=payment.source_currency,
        source_amount=payment.source_amount,
        fx_rate=payment.fx_rate,
        target_country=payment.target_country,
    )

    result_obj = await adapter.create_payment(payload)
    payment.provider = adapter.provider_name
    payment.provider_payment_id = result_obj.provider_payment_id
    payment.reference = result_obj.reference or payment.reference
    payment.submitted_at = now

    if result_obj.status == PaymentStatus.completed:
        payment.status = "completed"
        payment.completed_at = now
        await _capture_discount_offers(
            db, org=org, invoice=invoice, payment=payment, actor_id=user.id, now=now
        )
        if invoice and invoice.status.value in (
            "approved",
            "sent_to_erp",
            "posted_in_erp",
        ):
            await transition_invoice(
                db,
                invoice,
                InvoiceStatus.payment_scheduled,
                actor_id=user.id,
                action_name="invoice.payment_scheduled",
                details={"payment_id": str(payment.id), "result": "completed"},
            )
    elif result_obj.status in (PaymentStatus.submitted, PaymentStatus.processing):
        # Real money in flight; webhook will finalize.
        payment.status = result_obj.status.value
        if invoice and invoice.status.value in (
            "approved",
            "sent_to_erp",
            "posted_in_erp",
        ):
            await transition_invoice(
                db,
                invoice,
                InvoiceStatus.payment_scheduled,
                actor_id=user.id,
                action_name="invoice.payment_scheduled",
                details={"payment_id": str(payment.id), "result": result_obj.status.value},
            )
    else:
        # failed or cancelled
        payment.status = result_obj.status.value
        payment.failure_reason = result_obj.failure_reason
        payment.completed_at = now


async def _dispatch_run_payments(
    db: AsyncSession,
    *,
    run: PaymentRun,
    run_id: uuid.UUID,
    org: Organization,
    user: User,
    adapter: PaymentAdapter,
) -> dict:
    """Dispatch every still-`pending` payment on `run`, committing durably
    after each one, then roll up the run's final status across ALL its
    payments (not just the ones touched in this pass) and return the
    response payload.

    Shared by `execute_payment_run` (fresh `draft` claim) and
    `resume_payment_run` (an `executing` run stuck after a crash) — both
    already hold (and released) the row lock and have decided it's this
    call's turn to run the loop.

    Each payment is committed right after it's dispatched: a problem with
    payment N — including an uncaught error from a live FX/sanctions/processor
    adapter — can only roll back payment N's own (still-open) attempt, never
    the payments already recorded before it. That durability is what makes an
    `executing` run resumable instead of permanently stuck with real money
    moved but no local record of it.

    `adapter` is resolved by the caller via `_require_payment_adapter` BEFORE
    it claims the run, so an unsupported `settings.payments.provider` refuses
    with the run untouched instead of stranding it `executing`.
    """
    now = datetime.now(UTC)

    from app.services.audit_dispatch import dispatch_audit

    # Only payments this run hasn't attempted yet. Every payment starts
    # `pending`; on a resume, anything already `completed` / `failed` /
    # `submitted` / `processing` / `pending_compliance` from an earlier
    # (crashed) pass is left untouched — never re-dispatched to the processor.
    pay_result = await db.execute(
        select(Payment).where(Payment.payment_run_id == run_id, Payment.status == "pending")
    )
    pending_payments = pay_result.scalars().all()

    for payment in pending_payments:
        # Re-lock and re-check immediately before dispatching, mirroring the
        # reconciler's claim pattern (payment_reconciler.py). The bulk read
        # above is a plain SELECT with no lock — held across this whole loop
        # it would be released early anyway by the per-payment commit below —
        # so without this, two concurrent callers (two /resume calls, or a
        # /resume racing an in-flight /execute) both load the same pending
        # row and both dispatch it to the adapter. This is what makes a
        # second concurrent caller see the row already claimed and skip it
        # instead of double-charging the processor.
        await db.refresh(payment, with_for_update=True)
        if payment.status != "pending":
            continue
        try:
            await _execute_single_payment(
                db, payment=payment, org=org, adapter=adapter, user=user, now=now
            )
        except Exception as exc:  # noqa: BLE001
            # A live FX / sanctions / processor adapter can raise anything on
            # a network or API hiccup (bare RuntimeError, httpx errors, ...).
            # Recording THIS payment as failed — instead of letting the
            # exception unwind the whole request — is what keeps the other
            # payments in this run from being lost to a rollback.
            #
            # Log the exception TYPE only, never `str(exc)` / `exc_info` — a
            # live FX/sanctions/processor adapter can embed a partial account
            # number, IBAN, or PAN in its error string, and that must never
            # reach the log sink or this row (PII/banking-data-out-of-logs
            # invariant). Mirrors `card_issuance.py` / `payment_erp_sync.py`.
            logger.warning(
                "payment %s raised during payment-run dispatch; marking failed: %s",
                payment.id,
                exc.__class__.__name__,
            )
            payment.status = "failed"
            payment.failure_reason = f"unexpected_error:{exc.__class__.__name__}"
            payment.completed_at = now

        # Append-only audit trail for the money-movement event (project
        # invariant: every payment status transition writes a log row, and a
        # change that touches a regulated timestamp like `completed_at` is
        # Critical without one). PII-free: only ids, status, and the Decimal
        # amount as a string ever enter `details` — never bank/account values.
        await dispatch_audit(
            db,
            correlation_id=payment.correlation_id or run.id,
            organization_id=org.id,
            actor_id=user.id,
            action=f"payment.{payment.status}",
            entity_type="payment",
            entity_id=payment.id,
            details={
                "status": payment.status,
                "method": payment.method,
                "amount": str(payment.amount),
                "reference": payment.reference,
                "payment_run_id": str(run.id),
            },
        )

        # Durable per-payment commit. A crash or exception on the NEXT
        # payment can only roll back ITS OWN still-open transaction — this
        # one is already safely on disk.
        await db.commit()

    # Roll up over EVERY payment on the run — not just this pass — so a
    # resumed run's final status/counts reflect the whole run, not only the
    # subset that was still pending when this call started.
    all_result = await db.execute(select(Payment).where(Payment.payment_run_id == run_id))
    # A retried invoice carries several rows on this run; only the newest
    # attempt describes its outcome (see `active_run_payments`).
    all_payments = active_run_payments(all_result.scalars().all())
    # Bucketing + the status precedence live in `services/payment_runs` so the
    # status this PERSISTS and the rollup the run-detail / runs-list reads
    # REPORT can't drift apart.
    rollup = rollup_payment_statuses(p.status for p in all_payments)
    completed, failed, in_flight = rollup.completed, rollup.failed, rollup.in_flight
    cards_issued = sum(
        1 for p in all_payments if p.method == "virtual_card" and p.status == "completed"
    )

    run.status = rollup.run_status
    run.executed_at = now

    await dispatch_audit(
        db,
        correlation_id=run.id,
        organization_id=org.id,
        actor_id=user.id,
        action="payment_run.executed",
        entity_type="payment_run",
        entity_id=run.id,
        details={
            "status": run.status,
            "provider": adapter.provider_name,
            "payments_completed": completed,
            "payments_in_flight": in_flight,
            "payments_failed": failed,
            "cards_issued": cards_issued,
            "total_amount": str(run.total_amount or Decimal("0")),
        },
    )
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
        "cards_issued": cards_issued,
        "message": _execute_message(
            adapter.provider_name, completed, in_flight, failed, cards_issued
        ),
    }


@router.post("/runs/{run_id}/execute")
async def execute_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # The money-moving end of the payment SoD split. Defaults map to
    # admin/ap_manager/cfo (unchanged); split from run-approval / bank-change.
    user: User = Depends(require_permission(PERM_PAYMENT_EXECUTE)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Execute a draft payment run via the configured payment adapter.

    Each payment is dispatched to the org's configured processor (Modern
    Treasury for prod, mock for dev). The adapter returns either a
    `submitted`/`processing` status (real money in flight, terminal status
    arrives via webhook) or `completed`/`failed` immediately (mock). See
    `POST /runs/{run_id}/resume` if a run gets stuck in `executing` (e.g. a
    worker crashed mid-run) — this endpoint stays `draft`-only so a plain
    concurrent double-click can never race a still-genuinely-running
    execution (see the row-lock comment below).

    Run status:
      - `completed` — every payment reached `completed`
      - `partial`   — at least one succeeded, at least one failed
      - `failed`    — every payment failed
      - `submitted` — at least one payment is in flight (waiting on webhook)
    """
    # Lock the run row FOR UPDATE and atomically flip it out of `draft`
    # BEFORE the adapter loop. Without this, two concurrent /execute calls
    # both read `status == "draft"`, both pass the guard, and both dispatch
    # every payment to the processor — the adapter is charged twice for the
    # same rows (double-pay). The row lock serializes the two requests: the
    # first acquires the lock, re-checks `draft`, flips the run to
    # `executing`, and commits; the second blocks on the lock, then re-reads
    # the now-`executing` status and 409s before any money moves. (The
    # adapter call itself is also idempotency-keyed via
    # `PaymentPayload.correlation_id` — defense in depth for processors that
    # honor it, e.g. Modern Treasury / Column.)
    run = await _get_scoped_run(db, run_id, entity_id, for_update=True)
    if run.status != "draft":
        raise HTTPException(
            status_code=409, detail=f"Can only execute 'draft' runs, not '{run.status}'"
        )
    # Maker-checker: the user who created the run cannot also execute it (the
    # money-movement step). Default-on; per-org opt-out for single-operator
    # accounts. The role/permission split can't enforce this — one user holding
    # both perms (the default ap_manager) would otherwise run the whole payment
    # lifecycle solo.
    check_run_segregation(
        run.initiated_by,
        user.id,
        (org.settings or {}).get("payments"),
        action="execute",
    )
    if run.requires_cfo_approval and run.cfo_approved_at is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This run exceeds the org's CFO-approval threshold and is awaiting "
                "sign-off from a user with the CFO role."
            ),
        )

    # Resolve the processor BEFORE claiming the run. An unsupported
    # `settings.payments.provider` can dispatch nothing, so refusing here
    # leaves the run in `draft` and re-runnable once settings are fixed —
    # rather than stranding it `executing` behind a 500.
    adapter = _require_payment_adapter(org)

    # Claim the run: flip it to an in-flight status and commit so the lock
    # releases and any concurrent caller blocked above wakes to a non-draft
    # run (→ 409). The final rollup status overwrites `executing` at the end.
    run.status = "executing"
    await db.commit()

    return await _dispatch_run_payments(
        db, run=run, run_id=run_id, org=org, user=user, adapter=adapter
    )


@router.post("/runs/{run_id}/resume")
async def resume_payment_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # Resuming can dispatch real payments exactly like /execute — same gate.
    user: User = Depends(require_permission(PERM_PAYMENT_EXECUTE)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Resume a payment run stuck in `executing` — e.g. the worker process
    crashed partway through `execute_payment_run`'s per-payment loop.

    Because that loop commits durably after every payment, a run left in
    `executing` has every payment up to the crash point already safely
    recorded with its real outcome; only the still-`pending` ones (never
    attempted) get re-dispatched here — nothing already `completed` /
    `failed` / `submitted` / `processing` / `pending_compliance` is touched
    or re-sent to the processor.

    Deliberately a SEPARATE endpoint from `/execute` (which stays
    `draft`-only): a run that is still genuinely mid-execution is ALSO
    `executing`, and a bare "accept `executing` too" guard on `/execute`
    would let a concurrent call race an active run instead of only a
    crashed one. An operator calls this endpoint only after confirming the
    run is actually stuck (no progress for an implausible amount of time),
    not as a matter of course.
    """
    run = await _get_scoped_run(db, run_id, entity_id, for_update=True)
    if run.status != "executing":
        raise HTTPException(
            status_code=409,
            detail=f"Can only resume a run stuck 'executing', not '{run.status}'",
        )
    # Resuming dispatches real payments exactly like /execute — same
    # maker-checker gate. Without this, the run's own initiator could wait
    # for (or force) it into `executing` and resume-execute their own run
    # solo, after already being refused at /execute.
    check_run_segregation(
        run.initiated_by,
        user.id,
        (org.settings or {}).get("payments"),
        action="execute",
    )

    # Same pre-flight as /execute: refuse an unsupported processor before the
    # loop rather than 500-ing partway through it.
    adapter = _require_payment_adapter(org)

    # Release the row lock before the (potentially slow) per-payment loop —
    # no status change needed here, the run is already `executing`.
    await db.commit()

    return await _dispatch_run_payments(
        db, run=run, run_id=run_id, org=org, user=user, adapter=adapter
    )


@router.post("/runs/{run_id}/sync-erp")
async def retry_run_erp_sync(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # Re-running the sync can flip invoices `payment_scheduled → paid` — the
    # same money-state authority as executing a run or accepting a short
    # settlement, so the same gate.
    user: User = Depends(require_permission(PERM_PAYMENT_EXECUTE)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Re-run the ERP sync-back for a run whose settled payments didn't land.

    This is the **exit for a stranded invoice**. `services/payment_erp_sync` is
    the only path that flips `payment_scheduled → paid`, and it is dispatched
    exactly once per terminal event (run execute, payment webhook) —
    fire-and-forget onto a detached thread. If a leg fails there, the money has
    already moved but the invoice never advances, the ERP is never told, and
    **nothing re-invokes the sync**, because the reconciler backstop only
    re-dispatches payments it moves out of `submitted`/`processing`.

    Voiding is not an exit for that state: `POST /{payment_id}/void` returns the
    invoice to `approved`, which invites a second payment for money that already
    left. So the strand gets an explicit, audited re-run instead.

    A failed leg now also opens a de-duped `erp_reconciliation` exception naming
    this endpoint, so the operator reaches it from the queue rather than having
    to know the run id. Deliberately does NOT resolve that exception on success
    — `erp_reconciliation` is shared with the ERP-void path in
    `api/erp_webhook`, so auto-closing "the open one" could silently clear an
    unrelated reconciliation (the same reasoning `POST /{id}/settlement/accept`
    documents for `fraud_flag`). The human closes it after confirming.

    **Idempotent by construction, not by a claim**: the pass skips every payment
    that isn't `completed` and every invoice that isn't `payment_scheduled`, so
    a repeat call after a successful re-run writes no second transition. It
    moves no money — it only reports money that already moved.

    Read `transitioned`, not `synced`, to answer "did this recover anything".
    `synced` counts legs whose ERP-facing work completed, which stays TRUE for a
    settled payment whose invoice was already `paid` — so a repeat call reports
    the same `synced` count and `transitioned: 0`.

    Unlike the two dispatch sites this one AWAITS the pass, so the response
    carries the real per-leg counts instead of "queued".
    """
    from app.services.audit_dispatch import dispatch_audit
    from app.services.payment_erp_sync import _sync_payments

    run = await _get_scoped_run(db, run_id, entity_id)
    settled = (
        await db.execute(
            select(func.count())
            .select_from(Payment)
            .where(Payment.payment_run_id == run.id, Payment.status == "completed")
        )
    ).scalar() or 0
    if settled == 0:
        raise HTTPException(
            status_code=409,
            detail="This run has no settled payments to sync to the ERP.",
        )

    await dispatch_audit(
        db,
        correlation_id=run.id,
        organization_id=org.id,
        actor_id=user.id,
        action="payment_run.erp_sync_retried",
        entity_type="payment_run",
        entity_id=run.id,
        details={"status": run.status, "settled_payments": settled},
    )
    # Read what the pass needs off the ORM row BEFORE committing, so this
    # doesn't quietly depend on the session's `expire_on_commit=False` — an
    # expired attribute read from async SQLAlchemy is an implicit lazy load,
    # i.e. a MissingGreenlet rather than a clean failure.
    run_org_id = uuid.UUID(str(run.organization_id))
    run_ref = str(run.id)
    # Commit (and release the request session's transaction) BEFORE the pass —
    # it opens its own tenant session and updates the same invoice rows.
    await db.commit()

    result = await _sync_payments(run_id, run_org_id)
    return {
        "id": run_ref,
        "synced": result.synced,
        "transitioned": result.transitioned,
        "skipped": result.skipped,
        "held": result.held,
        "failed": result.failed,
    }


# Statuses a payment must be in for `/retry-failed` to consider re-attempting
# it. Both are terminal *failures*, and both are excluded from
# `uq_payments_one_live_per_invoice` — which is what lets the retry book a
# SECOND payment row against the same invoice at all. Being in one of these is
# necessary but nowhere near sufficient: `classify_payment_failure` still has to
# prove no order was created at the processor (see the endpoint docstring).
RETRYABLE_PAYMENT_STATUSES = ("failed", "cancelled")

# The run statuses that can carry a failed payment worth re-attempting. A
# `draft` run has never been dispatched (use `/execute`), an `executing` one is
# either mid-flight or crashed (use `/resume`), and a `cancelled` one has no
# payments left at all.
RETRYABLE_RUN_STATUSES = ("partial", "failed")


@router.post("/runs/{run_id}/retry-failed")
async def retry_failed_payments(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # Re-attempting dispatches real payments exactly like /execute — same gate.
    user: User = Depends(require_permission(PERM_PAYMENT_EXECUTE)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Re-attempt the safely-retryable FAILED payments of a `partial`/`failed` run.

    A payment run's failures were previously a dead end: the run settled on
    `partial`, and the only way forward was to hand-build a second run.

    **A retry books a NEW payment, it never re-arms the old one.**
    `Payment.correlation_id` is the PROCESSOR's idempotency key (see
    `payment_adapters/base.py`), so a re-attempt is genuinely a new order and
    needs a new one. Minting it onto the failed row — as this endpoint first
    did — also meant clearing `failure_reason`, `provider_payment_id`,
    `submitted_at` and `completed_at`, destroying the only handles anyone had
    for reconciling attempt #1 with the processor and overwriting two regulated
    money timestamps. Attempt #1 is therefore never written to at all; attempt
    #2 is an INSERT on the same run carrying `retry_of_payment_id`, and the run
    rollup counts the latest attempt per invoice
    (`services/payment_runs.active_run_payments`). `failed`/`cancelled` are
    outside `uq_payments_one_live_per_invoice`, so the second row is free to
    claim the invoice's live-payment slot.

    **What is re-attempted, and what is not.** Anything already `completed`,
    `submitted`, `processing` or `pending_compliance` is left exactly as it is
    and is never re-sent — the same guarantee `/resume` makes. A payment in
    `RETRYABLE_PAYMENT_STATUSES` is *skipped* (never re-sent, never mutated),
    with the reason surfaced in `skip_reasons`, when:

    - `invoice_not_payable` — the invoice is voided, re-rejected or already
      `done`, so paying it would move money against something nobody currently
      approves;
    - `invoice_has_blocking_exception` — an unresolved
      `PAYMENT_BLOCKING_EXCEPTION_TYPES` flag (duplicate / fraud_flag /
      line_total_mismatch). Run creation refuses these outright; this endpoint
      re-dispatches money days or weeks later, so a `fraud_flag` raised in the
      interim (a BEC bank-detail swap, an altered cheque off a Positive Pay
      return) has to stop the re-send here too. Same shared query
      (`payment_runs.blocked_invoice_ids`) so the two can't drift;
    - `needs_reconciliation` — we cannot prove the processor never accepted the
      original order (`classify_payment_failure`): a populated
      `provider_payment_id`, an `unexpected_error:*` / `*_transport_error:*` /
      `*_api_error:*` / `reconciler_max_age_exceeded*` reason, or any reason
      we don't recognise. Re-sending these under a fresh idempotency key is how
      one invoice gets paid twice. A human voids or reconciles first;
    - `net_amount_changed` — a credit memo applied since the run was built
      means the failed row's `amount` is no longer what the vendor is owed
      (`payment_runs.net_payable_amount`). The amount is never silently
      adjusted — a fresh run re-derives it through the full gate set;
    - `invoice_has_live_payment` — the invoice has since acquired another live
      payment. The savepoint around each insert is the backstop for the same
      conflict arriving as a race.

    **Idempotency.** The run is row-locked and claimed (`→ executing`) before
    anything is booked, so a double-click / concurrent retry blocks on the lock
    and then 409s against the non-retryable status — it can't produce a second
    dispatch pass. `_dispatch_run_payments` then re-locks each payment and skips
    any that is no longer `pending`.
    """
    run = await _get_scoped_run(db, run_id, entity_id, for_update=True)
    if run.status not in RETRYABLE_RUN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                "Can only retry a run that finished with failures "
                f"({' or '.join(RETRYABLE_RUN_STATUSES)}), not '{run.status}'"
            ),
        )
    # Re-attempting moves money exactly like /execute — same maker-checker gate
    # and the same CFO threshold. Skipping either here would turn `/retry-failed`
    # into a way around both.
    check_run_segregation(
        run.initiated_by,
        user.id,
        (org.settings or {}).get("payments"),
        action="execute",
    )
    if run.requires_cfo_approval and run.cfo_approved_at is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This run exceeds the org's CFO-approval threshold and is awaiting "
                "sign-off from a user with the CFO role."
            ),
        )

    # Same pre-flight as /execute and /resume: a run whose processor can't be
    # resolved retries nothing, so refuse before booking any retry attempt row.
    adapter = _require_payment_adapter(org)

    all_run_payments = (
        (await db.execute(select(Payment).where(Payment.payment_run_id == run_id))).scalars().all()
    )
    # An earlier attempt a previous retry already replaced is history — its
    # successor is what describes where the invoice stands.
    superseded = superseded_payment_ids(all_run_payments)
    failed_payments = [
        p
        for p in all_run_payments
        if p.status in RETRYABLE_PAYMENT_STATUSES and p.id not in superseded
    ]

    # Which of those invoices can legitimately take another attempt. Every gate
    # `create_payment_run_for_invoices` applies at run-creation time is re-run
    # here, because this endpoint dispatches real money days or weeks later and
    # nothing guarantees the world stayed still.
    invoice_ids = [p.invoice_id for p in failed_payments]
    invoices: dict[uuid.UUID, Invoice] = {}
    payable_ids: set[uuid.UUID] = set()
    blocked_ids: set[uuid.UUID] = set()
    occupied_ids: set[uuid.UUID] = set()
    if invoice_ids:
        invoices = {
            inv.id: inv
            for inv in (await db.execute(select(Invoice).where(Invoice.id.in_(invoice_ids))))
            .scalars()
            .all()
        }
        payable_ids = {
            iid for iid, inv in invoices.items() if inv.status.value in PAYABLE_INVOICE_STATUSES
        }
        blocked_ids = await blocked_invoice_ids(db, invoice_ids)
        occupied_ids = set(
            (
                await db.execute(
                    select(Payment.invoice_id).where(
                        Payment.invoice_id.in_(invoice_ids),
                        Payment.status.notin_(LIVE_PAYMENT_TERMINAL_STATUSES),
                    )
                )
            )
            .scalars()
            .all()
        )

    retried = 0
    skipped: list[str] = []
    for payment in failed_payments:
        if payment.invoice_id not in payable_ids:
            skipped.append("invoice_not_payable")
            continue
        if payment.invoice_id in blocked_ids:
            skipped.append("invoice_has_blocking_exception")
            continue
        if not is_retry_safe(payment):
            # We can't prove the processor never took the original order. A
            # fresh idempotency key here is a second real payment, not a retry.
            skipped.append("needs_reconciliation")
            continue
        if payment.invoice_id in occupied_ids:
            skipped.append("invoice_has_live_payment")
            continue
        # What the invoice is worth NOW. A credit memo applied while this sat
        # `failed` (credit_memos.py gates on neither invoice status nor an
        # existing payment) makes the failed row's amount stale; pay it and the
        # vendor is overpaid by the credit.
        net_amount = await net_payable_amount(db, invoices[payment.invoice_id])
        if net_amount != payment.amount:
            skipped.append("net_amount_changed")
            continue

        retry_payment = Payment(
            invoice_id=payment.invoice_id,
            entity_id=payment.entity_id,
            payment_run_id=run.id,
            amount=net_amount,
            method=payment.method,
            status="pending",
            # A new order at the processor, so a new idempotency key. The
            # failed attempt keeps its own — it is the only handle anyone has
            # for reconciling what that attempt did.
            correlation_id=uuid.uuid4(),
            retry_of_payment_id=payment.id,
        )
        try:
            # Savepoint per insert: the live-payment unique index is the
            # backstop for a conflict that appeared between the pre-check above
            # and this flush. Containing it keeps the outer transaction usable
            # so the remaining payments can still be re-attempted.
            async with db.begin_nested():
                db.add(retry_payment)
                await db.flush()
        except IntegrityError:
            db.expunge(retry_payment)
            skipped.append("invoice_has_live_payment")
            continue
        retried += 1

        from app.services.audit_dispatch import dispatch_audit

        # Links BOTH rows: `entity_id` is attempt #1 (the row whose meaning
        # changed — it is now superseded) and `retry_payment_id` names its
        # successor, so an auditor can walk the chain from either end. PII-free:
        # ids, the Decimal amount as a string, the rail, and the previous
        # failure reason (a processor error code, never account data).
        await dispatch_audit(
            db,
            correlation_id=retry_payment.correlation_id or run.id,
            organization_id=org.id,
            actor_id=user.id,
            action="payment.retried",
            entity_type="payment",
            entity_id=payment.id,
            details={
                "payment_run_id": str(run.id),
                "payment_id": str(payment.id),
                "retry_payment_id": str(retry_payment.id),
                "amount": str(retry_payment.amount),
                "method": retry_payment.method,
                "previous_failure_reason": payment.failure_reason,
            },
        )

    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=run.id,
        organization_id=org.id,
        actor_id=user.id,
        action="payment_run.retried",
        entity_type="payment_run",
        entity_id=run.id,
        details={
            "payments_retried": retried,
            "payments_skipped": len(skipped),
            "skip_reasons": sorted(set(skipped)),
            "previous_status": run.status,
        },
    )

    # Claim the run before dispatching, exactly like /execute: the commit
    # releases the row lock, and any concurrent retry/execute/resume that was
    # blocked on it wakes to `executing` and 409s.
    run.status = "executing"
    await db.commit()

    result = await _dispatch_run_payments(
        db, run=run, run_id=run_id, org=org, user=user, adapter=adapter
    )
    result["payments_retried"] = retried
    result["payments_skipped"] = len(skipped)
    result["skip_reasons"] = sorted(set(skipped))
    return result


def _execute_message(
    provider: str, completed: int, in_flight: int, failed: int, cards_issued: int = 0
) -> str:
    parts: list[str] = []
    if completed:
        parts.append(f"{completed} completed")
    if in_flight:
        parts.append(f"{in_flight} in flight")
    if failed:
        parts.append(f"{failed} failed")
    if cards_issued:
        parts.append(f"{cards_issued} card(s) issued")
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

    A verified signature authenticates the SENDER, not the CONTENT: a
    `completed` event proves the processor is talking to us about a payment
    we know, never that it moved the amount AP authorized. Every completion
    therefore runs `services/payment_settlement.verify_settlement` against
    the reported amount + currency, and a divergence opens a
    payment-blocking `fraud_flag` instead of being treated as a clean
    settlement. See `backend/docs/payments.md` § Settlement-amount
    verification.

    URL shape (configure in the processor's dashboard):
        https://app.com/api/payments/webhook/{tenant_slug}/{provider}
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.config import settings as app_settings
    from app.database import control_session_factory, get_tenant_engine

    # Bound the body BEFORE buffering it. The HMAC check happens inside
    # adapter.parse_webhook, well after this point, so an unauthenticated
    # attacker could otherwise POST an arbitrarily large payload and have it
    # read fully into memory before anything rejects it (memory-exhaustion
    # DoS on a public route). Reject on the declared Content-Length when
    # present, and re-check the actual read in case the header lied / was
    # absent (chunked). Processor status payloads are small JSON; cap
    # defaults to a few MB.
    max_bytes = app_settings.payment_webhook_max_bytes
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                logger.warning("Payment webhook rejected: body exceeds size cap")
                return
        except ValueError:
            logger.warning("Payment webhook rejected: invalid content-length")
            return

    body = await request.body()
    if len(body) > max_bytes:
        logger.warning("Payment webhook rejected: body exceeds size cap")
        return
    headers = {k: v for k, v in request.headers.items()}

    # The `mock` adapter's `parse_webhook` performs NO signature verification
    # (it exists only so test fixtures can simulate a status flip by calling the
    # adapter directly) and `mock` is the default provider for any tenant that
    # hasn't configured a real processor — seeded demo tenants and fresh signups
    # both land there. Serving it on this public, unauthenticated route would
    # accept forged status transitions. Mock never actually delivers webhooks, so
    # reject it here outright rather than relying on the downstream terminal-state
    # guard. Mirrors `cards.card_webhook`'s hardcoded `lithic`/`nium` allowlist
    # and the boot-time `mock` refusal on the billing webhook route.
    if provider == "mock":
        return

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

    try:
        adapter = get_payment_adapter(payment_config)
    except UnknownPaymentProviderError:
        # The tenant's configured provider name matches this URL but names a
        # processor we have no adapter for. Before `get_payment_adapter` failed
        # closed, this resolved to `mock` — whose `parse_webhook` verifies no
        # signature at all — so a single settings typo silently re-opened the
        # very hole the `provider == "mock"` early-return above exists to
        # close, under any other name. 204 like every other rejection path.
        logger.warning("Payment webhook rejected: tenant's configured provider is not supported")
        return
    event = adapter.parse_webhook(headers, body)
    if event is None:
        return  # bad signature, unrecognised event, or no-op

    # Dedup by the processor's event id. Webhook providers retry on any
    # non-2xx delivery; without this guard the same event could flip a
    # payment to `completed` twice and re-fire the ERP-sync dispatch.
    from app.services.webhook_security import (
        is_event_already_processed,
        release_event_claim,
    )

    claimed_event: str | None = None
    if event.event_id:
        if await is_event_already_processed(provider, event.event_id):
            return
        # Track the claim so the tenant-DB block below can release it if that
        # block raises: the Redis dedup claim is only durable once the status
        # transition commits, so a claim left set over a rolled-back txn would
        # dedup the provider's retry away for the full TTL — the payment would
        # then never reach terminal status. (Mirrors api/cards.py's discipline.)
        claimed_event = event.event_id
    else:
        # A provider (or a future adapter) that stopped populating event_id
        # can't be Redis-deduped. Make that an explicit, logged branch rather
        # than a silent short-circuit of the check: we proceed WITHOUT the
        # first-line dedup, and the terminal-state allowlist below (only
        # pending/submitted/processing are overwritable, under the FOR UPDATE
        # row lock) is the backstop that keeps a re-delivery from
        # double-completing a payment. If this warning ever fires in
        # production it means an adapter's parse_webhook needs an event_id.
        logger.warning(
            "[payment-webhook] empty event_id from provider=%s; skipping Redis "
            "dedup — relying on the terminal-state allowlist backstop",
            provider,
        )

    # Open a tenant-DB session to look up + update the Payment row. The whole
    # block is wrapped so that if anything below raises AFTER we claimed the
    # Redis dedup slot, we release the claim (see the `except` tail) and let the
    # exception propagate — the provider then retries (a 5xx), and the released
    # claim lets that retry actually reprocess instead of being deduped away for
    # the full TTL. Without this the transition would be dropped and the payment
    # would never reach terminal status.
    engine = get_tenant_engine(org.db_name)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            # Lock the row for the read-check-write below: the terminal-state
            # allowlist guard is a non-atomic read→check→write, and the
            # reconciler (a separate code path that doesn't share the Redis event
            # dedup) or a second concurrent delivery could otherwise interleave
            # between the check and the UPDATE. FOR UPDATE serialises them.
            pay_result = await db.execute(
                select(Payment)
                .where(Payment.provider_payment_id == event.provider_payment_id)
                .with_for_update()
            )
            payment = pay_result.scalar_one_or_none()
            if not payment:
                return  # late retry of a payment we don't have

            # Only a genuinely in-flight payment may be advanced by a webhook.
            # Webhooks arrive out of order and re-deliveries can land hours after
            # a status already settled, so we use an allowlist of overwritable
            # states rather than a blocklist of terminal ones — a blocklist
            # silently lets through any state it forgot to name. In particular a
            # late `completed` must NOT resurrect a `voided` / `cancelled` /
            # `pending_compliance` payment (which would flip money back on with
            # no audit row). `completed` / `failed` are already terminal too.
            if payment.status not in ("pending", "submitted", "processing"):
                return

            previous_status = payment.status
            payment.status = event.status.value
            if event.reference:
                payment.reference = event.reference
            if event.failure_reason:
                payment.failure_reason = event.failure_reason
            if payment.status in ("completed", "failed", "cancelled"):
                payment.completed_at = datetime.now(UTC)

            # Settlement-amount verification. A `provider_payment_id` proves
            # WHICH payment this event is about; it does not prove the
            # processor moved the amount on the instruction. Only `completed`
            # is checked — a `failed` / `cancelled` event moved no money, so
            # whatever figure it echoes reconciles against nothing.
            #
            # The invoice is loaded once here and reused for the discount
            # capture below (which used to fetch it itself): the target leg is
            # denominated in the INVOICE's currency, which the Payment row
            # doesn't carry, and a discrepancy needs the invoice to hang its
            # queue entry on.
            settlement: SettlementVerification | None = None
            settled_invoice: Invoice | None = None
            realized_fx: Decimal | None = None
            if payment.status == "completed":
                settled_invoice = (
                    await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
                ).scalar_one_or_none()
                # A rail whose event body carries no figure (Dwolla's bare
                # `{id, topic, resourceId}` envelope) gets one more chance
                # before we record a blind spot: ask the processor. This is
                # the async re-fetch `parse_webhook` deliberately cannot do —
                # that path is synchronous and on the signature-verification
                # line, so it must not make a network call.
                #
                # Guarded on every axis. An adapter without the capability
                # returns `available=False` from the base implementation, and
                # any failure at all leaves the settlement exactly where it
                # was — `unverified` — because a settlement fetch must never
                # break the webhook that is recording money movement.
                reported_amount, reported_currency = event.amount, event.currency
                if reported_amount is None and payment.provider_payment_id:
                    try:
                        report = await adapter.fetch_settlement(payment.provider_payment_id)
                    except Exception as exc:  # noqa: BLE001 - best-effort by contract
                        logger.warning(
                            "settlement fetch failed for payment=%s: %s",
                            payment.id,
                            exc.__class__.__name__,
                        )
                    else:
                        if report.available and report.amount is not None:
                            reported_amount = report.amount
                            reported_currency = report.currency

                settlement = verify_settlement(
                    reported_amount=reported_amount,
                    reported_currency=reported_currency,
                    target_amount=payment.amount,
                    target_currency=(settled_invoice.currency if settled_invoice else None),
                    source_amount=payment.source_amount,
                    source_currency=payment.source_currency,
                )
                # Persist what the rail says it moved, beside what AP
                # authorized. The audit row below is the immutable evidence;
                # this is the queryable money state the ERP sync reads to
                # decide whether the invoice may be marked `paid`
                # (`payment_settlement.settlement_coverage`).
                #
                # A rail that reported nothing leaves the columns NULL rather
                # than writing a zero: NULL means "no figure on record" and
                # fails OPEN in the coverage check, while a 0 would read as a
                # total shortfall and hold every such invoice forever.
                if settlement.settled_amount is not None:
                    payment.settled_amount = settlement.settled_amount
                    payment.settled_currency = settlement.settled_currency

                # Realized FX gain/loss, at the moment a foreign-currency
                # invoice actually settles. The liability was accrued at the
                # rate locked on the invoice when its reporting amount was
                # materialized; what moved is `source_amount` in the home
                # currency, and the difference is realized here and nowhere
                # else. `None` for a domestic payment, an invoice with no
                # accrual rate, or a same-currency settlement — a zero would
                # claim we measured and found no exposure.
                if settled_invoice is not None:
                    realized_fx = realized_fx_gain_loss_for_settlement(
                        invoice_amount=settled_invoice.amount,
                        invoice_currency=settled_invoice.currency,
                        reporting_currency=settled_invoice.reporting_currency,
                        reporting_fx_rate=settled_invoice.reporting_fx_rate,
                        paid_source_amount=payment.source_amount,
                        paid_source_currency=payment.source_currency,
                    )

            # Append-only audit trail for the webhook-driven status transition.
            # This is the production money-movement event — the processor's
            # webhook is what flips a real payment to `completed`/`failed` and
            # sets the regulated `completed_at`. Per the project invariant, a
            # status change touching `completed_at` without an audit row is
            # Critical. Actor is None (system-initiated by the processor, not a
            # user). PII-free: only ids, status, the Decimal amount as a string,
            # and the reference ever enter `details`.
            from app.services.audit_dispatch import dispatch_audit

            await dispatch_audit(
                db,
                correlation_id=payment.correlation_id or uuid.uuid4(),
                organization_id=org.id,
                actor_id=None,
                action=f"payment.{payment.status}",
                entity_type="payment",
                entity_id=payment.id,
                details={
                    "status": payment.status,
                    "previous_status": previous_status or "unknown",
                    "method": payment.method,
                    "amount": str(payment.amount),
                    "reference": payment.reference,
                    "source": "webhook",
                    "provider": provider,
                    "payment_run_id": (
                        str(payment.payment_run_id) if payment.payment_run_id else None
                    ),
                    # The settlement verdict rides the SAME append-only row
                    # that records the money moving. The exception row below
                    # is mutable and gets resolved; this is the WORM-shipped
                    # evidence of what the processor said it settled, and it
                    # is written on every completion — matched, mismatched,
                    # and unverified alike — so a rail that reports no amount
                    # is a visible blind spot rather than a silent one.
                    **({"settlement": settlement.as_details()} if settlement else {}),
                    # Exact decimal string, never a float. Absent (not zero)
                    # when there is no FX exposure to measure.
                    **(
                        {"realized_fx_gain_loss": str(realized_fx)}
                        if realized_fx is not None
                        else {}
                    ),
                },
            )

            # A real (non-mock) rail typically confirms via THIS webhook, not
            # the synchronous leg of `_execute_single_payment` — ACH/wire sit
            # `submitted`/`processing` until the processor calls back. This is
            # the settlement moment for those payments, so it's also where an
            # accepted discount offer paid at its discounted payoff gets
            # recognized (mirrors the synchronous completion leg's call) —
            # unless the settlement itself didn't reconcile.
            if payment.status == "completed":
                if settlement is not None and settlement.is_discrepancy:
                    # Flag, and do NOT capture the discount: the payoff match
                    # runs against `payment.amount` — OUR authorized figure,
                    # which the rail has just contradicted — so capturing here
                    # would permanently mark savings realized on a number in
                    # dispute and misreport them to the CFO.
                    #
                    # The payment itself stays `completed`: money moved, and
                    # refusing to record that does not un-move it. The control
                    # is the blocking exception (mirroring how positive_pay
                    # flags an altered cheque and bank_reconciliation flags a
                    # divergent debit — both record and flag, neither rewrites
                    # the settlement).
                    await _open_settlement_mismatch_exception(
                        db,
                        payment=payment,
                        invoice=settled_invoice,
                        org=org,
                        verification=settlement,
                    )
                else:
                    await _capture_discount_offers(
                        db,
                        org=org,
                        payment=payment,
                        actor_id=None,
                        now=payment.completed_at or datetime.now(UTC),
                        invoice=settled_invoice,
                    )

            run_id = payment.payment_run_id if payment.status == "completed" else None
            await db.commit()
    except Exception:
        # The dedup claim guards a side effect that just rolled back — release
        # it so the provider's retry can reprocess (otherwise the money-state
        # transition is dropped for the full TTL). Re-raise so the provider
        # actually retries. Mirrors api/cards.py's card_webhook.
        if claimed_event is not None:
            await release_event_claim(provider, claimed_event)
        raise

    # ERP sync runs after the DB commit so it sees the latest status.
    if run_id:
        from app.services.payment_erp_sync import dispatch_payment_sync

        await dispatch_payment_sync(run_id, uuid.UUID(str(org.id)))
