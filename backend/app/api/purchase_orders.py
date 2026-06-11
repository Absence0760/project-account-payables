"""Purchase Order endpoints — list, detail, sync from ERP."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    get_current_user,
    get_org_id,
    require_roles,
)
from app.api.pagination import PaginationParams, paginated, pagination_params
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.procurement import POLineItem, PurchaseOrder
from app.models.user import User
from app.models.vendor import Vendor
from app.tenant import get_tenant, get_tenant_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


def _line_item_dict(li: POLineItem) -> dict:
    return {
        "id": str(li.id),
        "description": li.description,
        "quantity": float(li.quantity) if li.quantity else None,
        "unit_price": float(li.unit_price) if li.unit_price else None,
        "total": float(li.total) if li.total else None,
    }


@router.get("")
async def list_purchase_orders(
    search: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    vendor_id: str | None = None,
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    base = select(PurchaseOrder)
    if search:
        pattern = f"%{search}%"
        base = base.where(PurchaseOrder.po_number.ilike(pattern))
    if status_filter:
        base = base.where(PurchaseOrder.status == status_filter)
    if vendor_id:
        base = base.where(PurchaseOrder.vendor_id == uuid.UUID(vendor_id))

    total_q = await db.execute(select(func.count()).select_from(base.subquery()))
    total = int(total_q.scalar() or 0)

    paged = (
        base.options(selectinload(PurchaseOrder.line_items))
        .order_by(PurchaseOrder.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    result = await db.execute(paged)
    pos = result.scalars().all()

    vendor_ids = {po.vendor_id for po in pos if po.vendor_id}
    vendor_names: dict[str, str] = {}
    if vendor_ids:
        v_result = await db.execute(select(Vendor).where(Vendor.id.in_(vendor_ids)))
        for v in v_result.scalars().all():
            vendor_names[str(v.id)] = v.name

    return paginated(
        [
            {
                "id": str(po.id),
                "po_number": po.po_number,
                "vendor_id": str(po.vendor_id) if po.vendor_id else None,
                "vendor_name": vendor_names.get(str(po.vendor_id)) if po.vendor_id else None,
                "total": float(po.total),
                "status": po.status,
                "line_items": [_line_item_dict(li) for li in po.line_items],
                "created_at": po.created_at.isoformat() if po.created_at else "",
            }
            for po in pos
        ],
        total,
        pagination,
    )


@router.get("/{po_id}")
async def get_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Single PO with line items + the invoices that reference its number."""
    result = await db.execute(
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.line_items))
        .where(PurchaseOrder.id == po_id)
    )
    po = result.scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    vendor_name: str | None = None
    if po.vendor_id:
        v = await db.execute(select(Vendor.name).where(Vendor.id == po.vendor_id))
        vendor_name = v.scalar_one_or_none()

    inv_q = await db.execute(
        select(Invoice).where(Invoice.po_number == po.po_number).order_by(Invoice.created_at.desc())
    )
    linked_invoices = [
        {
            "id": str(inv.id),
            "invoice_number": inv.invoice_number,
            "vendor_name": inv.vendor_name,
            "amount": float(inv.amount) if inv.amount else 0.0,
            "status": inv.status.value if hasattr(inv.status, "value") else inv.status,
        }
        for inv in inv_q.scalars().all()
    ]

    return {
        "id": str(po.id),
        "po_number": po.po_number,
        "vendor_id": str(po.vendor_id) if po.vendor_id else None,
        "vendor_name": vendor_name,
        "total": float(po.total),
        "status": po.status,
        "line_items": [_line_item_dict(li) for li in po.line_items],
        "linked_invoices": linked_invoices,
        "created_at": po.created_at.isoformat() if po.created_at else "",
    }


@router.post("/sync-erp")
async def sync_pos_from_erp(
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Pull purchase orders from the connected ERP via its adapter."""
    erp_config = (org.settings or {}).get("erp")
    if not erp_config:
        raise HTTPException(status_code=400, detail="No ERP configured")

    # Lazy-import adapter modules so the @register_adapter decorator
    # populates the dispatcher registry. Same pattern as vendors.py.
    import app.services.erp_adapters.dynamics_365_bc  # noqa: F401
    import app.services.erp_adapters.merge_dev  # noqa: F401
    import app.services.erp_adapters.mock_adapter  # noqa: F401
    import app.services.erp_adapters.netsuite  # noqa: F401
    from app.services.erp_adapters import get_erp_adapter

    adapter = get_erp_adapter(erp_config)
    try:
        erp_pos = await adapter.list_pos()
    except Exception as exc:
        logger.exception("ERP list_pos failed for org %s", org_id)
        raise HTTPException(
            status_code=502,
            detail=f"ERP request failed: {type(exc).__name__}",
        ) from exc

    # Vendor lookup map for linking POs to existing vendor rows.
    v_result = await db.execute(select(Vendor).where(Vendor.organization_id == org_id))
    vendor_map = {v.name.lower(): v.id for v in v_result.scalars().all()}

    created = 0
    skipped = 0
    for erp_po in erp_pos:
        existing = await db.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.po_number == erp_po.po_number,
                PurchaseOrder.organization_id == org_id,
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        vendor_id = vendor_map.get(erp_po.vendor_name.lower()) if erp_po.vendor_name else None

        po = PurchaseOrder(
            po_number=erp_po.po_number,
            vendor_id=vendor_id,
            total=erp_po.total,
            status=erp_po.status,
            organization_id=org_id,
        )
        db.add(po)
        await db.flush()

        for line in erp_po.line_items:
            db.add(
                POLineItem(
                    po_id=po.id,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    total=line.total,
                )
            )

        created += 1

    await db.commit()
    return {
        "success": True,
        "message": f"Synced {created} new POs, {skipped} already exist",
        "created": created,
        "skipped": skipped,
        "adapter": adapter.erp_type,
    }
