"""Credit memo endpoints — list, create, apply, void."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.api.pagination import PaginationParams, pagination_params
from app.models.credit_memo import CreditMemo
from app.models.invoice import Invoice
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.credit_memo import (
    CreditMemoApply,
    CreditMemoCreate,
    CreditMemoListResponse,
    CreditMemoResponse,
)
from app.services.audit_dispatch import dispatch_audit
from app.tenant import apply_entity_scope, get_entity_id, get_tenant_db

router = APIRouter(prefix="/credit-memos", tags=["credit-memos"])

# Shared by both application paths (create-with-invoice_id and /apply) so the
# two can't drift. Neither names the vendor — an authenticated AP user already
# knows the invoice, and the detail only has to say what to fix.
_VENDOR_MISMATCH_DETAIL = "Credit memo vendor does not match invoice vendor"
_VENDOR_UNRESOLVED_DETAIL = (
    "The invoice has no linked vendor, so the credit memo's vendor cannot be "
    "verified. Resolve the invoice's vendor first (re-save its vendor on the "
    "invoice), then apply the credit."
)


def _assert_vendor_matches(invoice: Invoice, vendor_id: uuid.UUID) -> None:
    """Refuse to credit an invoice unless its vendor PROVABLY matches the memo's.

    Fail-closed on a NULL ``Invoice.vendor_id``. A missing link does not mean
    "any vendor" — it means the invoice's vendor cannot be established, and a
    credit applied there reduces a balance nobody can attribute. Treating NULL
    as permissive (the old ``if invoice.vendor_id and …`` shape) let one
    vendor's credit memo be applied against another vendor's invoice for every
    invoice created without extraction.

    ``create_invoice`` and the vendor-name path of ``update_invoice`` now
    resolve the link, so new invoices satisfy this; a pre-existing unlinked
    invoice is resolved by re-saving its vendor name.
    """
    if invoice.vendor_id is None:
        raise HTTPException(status_code=409, detail=_VENDOR_UNRESOLVED_DETAIL)
    if invoice.vendor_id != vendor_id:
        raise HTTPException(status_code=409, detail=_VENDOR_MISMATCH_DETAIL)


def _to_response(
    memo: CreditMemo,
    *,
    vendor_name: str | None = None,
    invoice_number: str | None = None,
) -> CreditMemoResponse:
    return CreditMemoResponse(
        id=str(memo.id),
        memo_number=memo.memo_number,
        vendor_id=str(memo.vendor_id),
        vendor_name=vendor_name,
        invoice_id=str(memo.invoice_id) if memo.invoice_id else None,
        invoice_number=invoice_number,
        amount=memo.amount,  # Decimal — MoneyAmount serialises to a JSON number
        currency=memo.currency,
        issued_date=memo.issued_date.isoformat() if memo.issued_date else None,
        reason=memo.reason,
        status=memo.status,
        applied_at=memo.applied_at.isoformat() if memo.applied_at else None,
        applied_by=memo.applied_by,
        created_at=memo.created_at.isoformat() if memo.created_at else "",
    )


@router.get("", response_model=CreditMemoListResponse)
async def list_credit_memos(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
    status_filter: str | None = Query(None, alias="status"),
    pagination: PaginationParams = Depends(pagination_params),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    base = apply_entity_scope(select(CreditMemo), CreditMemo, entity_id)
    if status_filter:
        base = base.where(CreditMemo.status == status_filter)

    total_q = await db.execute(select(func.count()).select_from(base.subquery()))
    total = int(total_q.scalar() or 0)

    paged = apply_entity_scope(
        select(CreditMemo, Vendor.name, Invoice.invoice_number)
        .outerjoin(Vendor, CreditMemo.vendor_id == Vendor.id)
        .outerjoin(Invoice, CreditMemo.invoice_id == Invoice.id)
        .order_by(CreditMemo.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit),
        CreditMemo,
        entity_id,
    )
    if status_filter:
        paged = paged.where(CreditMemo.status == status_filter)
    result = await db.execute(paged)
    items = [
        _to_response(memo, vendor_name=vendor_name, invoice_number=invoice_number)
        for memo, vendor_name, invoice_number in result.all()
    ]
    return CreditMemoListResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


@router.post("", response_model=CreditMemoResponse, status_code=status.HTTP_201_CREATED)
async def create_credit_memo(
    body: CreditMemoCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    vendor_uuid = uuid.UUID(body.vendor_id)
    vendor_result = await db.execute(select(Vendor).where(Vendor.id == vendor_uuid))
    vendor = vendor_result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    invoice_uuid: uuid.UUID | None = None
    invoice_number: str | None = None
    if body.invoice_id:
        invoice_uuid = uuid.UUID(body.invoice_id)
        # Lock the invoice row for the duration of the txn so two concurrent
        # credit applies against the same invoice serialize through the
        # over-application guard below (the invoice is the natural
        # serialization point for "credits applied to this invoice").
        inv_result = await db.execute(
            select(Invoice).where(Invoice.id == invoice_uuid).with_for_update()
        )
        invoice = inv_result.scalar_one_or_none()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")
        # Same guards as the /apply path — a credit applied at creation time must
        # match the invoice's vendor and stay within its remaining balance.
        _assert_vendor_matches(invoice, vendor_uuid)
        # Same currency guard as the /apply path — the remaining-balance math
        # below subtracts the memo amount from the invoice amount directly, so a
        # EUR memo created against a USD invoice would silently mix currencies
        # and corrupt the balance.
        if body.currency and invoice.currency and body.currency != invoice.currency:
            raise HTTPException(
                status_code=409,
                detail="Credit memo currency does not match invoice currency",
            )
        already_applied = (
            await db.execute(
                select(func.coalesce(func.sum(CreditMemo.amount), Decimal("0"))).where(
                    CreditMemo.invoice_id == invoice_uuid,
                    CreditMemo.status == "applied",
                )
            )
        ).scalar_one()
        remaining = invoice.amount - already_applied
        if body.amount > remaining:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Credit memo amount exceeds the invoice's remaining creditable "
                    f"balance ({remaining})"
                ),
            )
        invoice_number = invoice.invoice_number

    memo = CreditMemo(
        memo_number=body.memo_number,
        vendor_id=vendor_uuid,
        invoice_id=invoice_uuid,
        amount=body.amount,
        currency=body.currency,
        issued_date=body.issued_date,
        reason=body.reason,
        status="applied" if invoice_uuid else "open",
        applied_at=datetime.now(UTC) if invoice_uuid else None,
        applied_by=user.full_name if invoice_uuid else None,
        organization_id=org_id,
        # Credit memo follows the vendor it credits (multi-entity Phase 2).
        entity_id=vendor.entity_id,
    )
    db.add(memo)
    await db.flush()
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="credit_memo.created",
        entity_type="credit_memo",
        entity_id=memo.id,
        details={
            "memo_number": memo.memo_number,
            "vendor_id": str(memo.vendor_id),
            "invoice_id": str(memo.invoice_id) if memo.invoice_id else None,
            "amount": str(memo.amount),  # string-Decimal, never float
            "currency": memo.currency,
            "status": memo.status,
        },
    )
    await db.commit()
    await db.refresh(memo)
    return _to_response(memo, vendor_name=vendor.name, invoice_number=invoice_number)


@router.post("/{memo_id}/apply", response_model=CreditMemoResponse)
async def apply_credit_memo(
    memo_id: uuid.UUID,
    body: CreditMemoApply,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(select(CreditMemo).where(CreditMemo.id == memo_id))
    memo = result.scalar_one_or_none()
    if not memo:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    if memo.status != "open":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot apply a credit memo in '{memo.status}' status",
        )

    invoice_uuid = uuid.UUID(body.invoice_id)
    # Row-lock the invoice so concurrent applies to the same invoice serialize
    # through the over-application guard (see create_credit_memo for the
    # rationale). Without this, two applies can both read the same
    # already-applied sum and both pass, over-crediting the invoice.
    inv_result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_uuid).with_for_update()
    )
    invoice = inv_result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_vendor_matches(invoice, memo.vendor_id)
    # Currencies must match — the remaining-balance arithmetic below subtracts
    # the memo amount from the invoice amount directly, so applying a EUR memo to
    # a USD invoice would silently mix currencies and corrupt the balance.
    if memo.currency and invoice.currency and memo.currency != invoice.currency:
        raise HTTPException(
            status_code=409,
            detail="Credit memo currency does not match invoice currency",
        )

    # Over-application guard: the sum of credits applied to an invoice can never
    # exceed what's owed on it. A credit beyond the invoice balance would create
    # a negative payable — money out of nowhere. Compute the remaining
    # creditable balance from already-applied memos (Decimal, never float).
    already_applied = (
        await db.execute(
            select(func.coalesce(func.sum(CreditMemo.amount), Decimal("0"))).where(
                CreditMemo.invoice_id == invoice_uuid,
                CreditMemo.status == "applied",
                CreditMemo.id != memo.id,
            )
        )
    ).scalar_one()
    remaining = invoice.amount - already_applied
    if memo.amount > remaining:
        raise HTTPException(
            status_code=409,
            detail=(
                "Credit memo amount exceeds the invoice's remaining creditable "
                f"balance ({remaining})"
            ),
        )

    memo.invoice_id = invoice_uuid
    memo.status = "applied"
    memo.applied_at = datetime.now(UTC)
    memo.applied_by = user.full_name
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="credit_memo.applied",
        entity_type="credit_memo",
        entity_id=memo.id,
        details={
            "memo_number": memo.memo_number,
            "vendor_id": str(memo.vendor_id),
            "invoice_id": str(invoice_uuid),
            "amount": str(memo.amount),  # string-Decimal, never float
            "currency": memo.currency,
            "remaining_before": str(remaining),
        },
    )
    await db.commit()
    await db.refresh(memo)

    vendor_result = await db.execute(select(Vendor.name).where(Vendor.id == memo.vendor_id))
    vendor_name = vendor_result.scalar_one_or_none()
    return _to_response(memo, vendor_name=vendor_name, invoice_number=invoice.invoice_number)


@router.post("/{memo_id}/void", response_model=CreditMemoResponse)
async def void_credit_memo(
    memo_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(select(CreditMemo).where(CreditMemo.id == memo_id))
    memo = result.scalar_one_or_none()
    if not memo:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    if memo.status == "applied":
        raise HTTPException(
            status_code=409, detail="Applied credit memos cannot be voided (immutable for audit)"
        )

    memo.status = "void"
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="credit_memo.voided",
        entity_type="credit_memo",
        entity_id=memo.id,
        details={
            "memo_number": memo.memo_number,
            "vendor_id": str(memo.vendor_id),
            "amount": str(memo.amount),  # string-Decimal, never float
            "currency": memo.currency,
        },
    )
    await db.commit()
    await db.refresh(memo)

    vendor_result = await db.execute(select(Vendor.name).where(Vendor.id == memo.vendor_id))
    vendor_name = vendor_result.scalar_one_or_none()
    return _to_response(memo, vendor_name=vendor_name)
