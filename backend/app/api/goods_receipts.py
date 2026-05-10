"""Goods Receipt endpoints — list + detail (linked PO + line items)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.models.procurement import GoodsReceipt, PurchaseOrder
from app.models.user import User
from app.tenant import get_tenant_db

router = APIRouter(prefix="/goods-receipts", tags=["goods-receipts"])


def _line_dict(li) -> dict:
    return {
        "id": str(li.id),
        "description": li.description,
        "quantity_received": float(li.quantity_received) if li.quantity_received else None,
    }


@router.get("")
async def list_goods_receipts(
    po_id: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    base = select(GoodsReceipt)
    if po_id:
        base = base.where(GoodsReceipt.po_id == uuid.UUID(po_id))
    if status_filter:
        base = base.where(GoodsReceipt.status == status_filter)

    total_q = await db.execute(select(func.count()).select_from(base.subquery()))
    total = int(total_q.scalar() or 0)

    paged = (
        base.options(selectinload(GoodsReceipt.line_items))
        .order_by(GoodsReceipt.received_date.desc().nullslast(), GoodsReceipt.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(paged)
    grs = result.scalars().all()

    # Look up PO numbers for the rendered set so the table can show
    # "GR-123 → PO-2024-005" without a separate fetch per row.
    po_ids = {gr.po_id for gr in grs if gr.po_id}
    po_numbers: dict[str, str] = {}
    if po_ids:
        po_q = await db.execute(select(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids)))
        for po in po_q.scalars().all():
            po_numbers[str(po.id)] = po.po_number

    return {
        "items": [
            {
                "id": str(gr.id),
                "gr_number": gr.gr_number,
                "po_id": str(gr.po_id) if gr.po_id else None,
                "po_number": po_numbers.get(str(gr.po_id)) if gr.po_id else None,
                "received_date": gr.received_date.isoformat() if gr.received_date else None,
                "status": gr.status,
                "line_count": len(gr.line_items),
                "created_at": gr.created_at.isoformat() if gr.created_at else "",
            }
            for gr in grs
        ],
        "total": total,
    }


@router.get("/{gr_id}")
async def get_goods_receipt(
    gr_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Single GR with line items + the PO it's against."""
    result = await db.execute(
        select(GoodsReceipt)
        .options(selectinload(GoodsReceipt.line_items))
        .where(GoodsReceipt.id == gr_id)
    )
    gr = result.scalar_one_or_none()
    if not gr:
        raise HTTPException(status_code=404, detail="Goods receipt not found")

    po_number: str | None = None
    if gr.po_id:
        po_q = await db.execute(
            select(PurchaseOrder.po_number).where(PurchaseOrder.id == gr.po_id)
        )
        po_number = po_q.scalar_one_or_none()

    return {
        "id": str(gr.id),
        "gr_number": gr.gr_number,
        "po_id": str(gr.po_id) if gr.po_id else None,
        "po_number": po_number,
        "received_date": gr.received_date.isoformat() if gr.received_date else None,
        "status": gr.status,
        "line_items": [_line_dict(li) for li in gr.line_items],
        "created_at": gr.created_at.isoformat() if gr.created_at else "",
    }
