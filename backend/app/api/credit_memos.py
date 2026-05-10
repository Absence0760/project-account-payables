"""Credit memo endpoints — list, create, apply, void."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
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
from app.tenant import get_tenant_db

router = APIRouter(prefix="/credit-memos", tags=["credit-memos"])


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
        amount=float(memo.amount),
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
):
    query = select(CreditMemo, Vendor.name, Invoice.invoice_number).outerjoin(
        Vendor, CreditMemo.vendor_id == Vendor.id
    ).outerjoin(Invoice, CreditMemo.invoice_id == Invoice.id)
    if status_filter:
        query = query.where(CreditMemo.status == status_filter)
    query = query.order_by(CreditMemo.created_at.desc())
    result = await db.execute(query)
    rows = result.all()
    items = [
        _to_response(memo, vendor_name=vendor_name, invoice_number=invoice_number)
        for memo, vendor_name, invoice_number in rows
    ]
    return CreditMemoListResponse(items=items, total=len(items))


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
        inv_result = await db.execute(select(Invoice).where(Invoice.id == invoice_uuid))
        invoice = inv_result.scalar_one_or_none()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")
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
    )
    db.add(memo)
    await db.commit()
    await db.refresh(memo)
    return _to_response(memo, vendor_name=vendor.name, invoice_number=invoice_number)


@router.post("/{memo_id}/apply", response_model=CreditMemoResponse)
async def apply_credit_memo(
    memo_id: uuid.UUID,
    body: CreditMemoApply,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
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
    inv_result = await db.execute(select(Invoice).where(Invoice.id == invoice_uuid))
    invoice = inv_result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.vendor_id and invoice.vendor_id != memo.vendor_id:
        raise HTTPException(
            status_code=409,
            detail="Credit memo vendor does not match invoice vendor",
        )

    memo.invoice_id = invoice_uuid
    memo.status = "applied"
    memo.applied_at = datetime.now(UTC)
    memo.applied_by = user.full_name
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
    await db.commit()
    await db.refresh(memo)

    vendor_result = await db.execute(select(Vendor.name).where(Vendor.id == memo.vendor_id))
    vendor_name = vendor_result.scalar_one_or_none()
    return _to_response(memo, vendor_name=vendor_name)
