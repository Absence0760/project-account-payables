"""Purchase Order endpoints — list, sync from ERP."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    get_current_user,
    get_org_id,
    require_roles,
)
from app.models.organization import Organization
from app.models.procurement import POLineItem, PurchaseOrder
from app.models.user import User
from app.models.vendor import Vendor
from app.tenant import get_tenant, get_tenant_db

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


@router.get("")
async def list_purchase_orders(
    search: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    vendor_id: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    query = select(PurchaseOrder).options(selectinload(PurchaseOrder.line_items))
    if search:
        pattern = f"%{search}%"
        query = query.where(PurchaseOrder.po_number.ilike(pattern))
    if status_filter:
        query = query.where(PurchaseOrder.status == status_filter)
    if vendor_id:
        query = query.where(PurchaseOrder.vendor_id == uuid.UUID(vendor_id))

    query = query.order_by(PurchaseOrder.created_at.desc())
    result = await db.execute(query)
    pos = result.scalars().all()

    # Look up vendor names
    vendor_ids = {po.vendor_id for po in pos if po.vendor_id}
    vendor_names: dict[str, str] = {}
    if vendor_ids:
        v_result = await db.execute(select(Vendor).where(Vendor.id.in_(vendor_ids)))
        for v in v_result.scalars().all():
            vendor_names[str(v.id)] = v.name

    return [
        {
            "id": str(po.id),
            "po_number": po.po_number,
            "vendor_id": str(po.vendor_id) if po.vendor_id else None,
            "vendor_name": vendor_names.get(str(po.vendor_id)) if po.vendor_id else None,
            "total": float(po.total),
            "status": po.status,
            "line_items": [
                {
                    "id": str(li.id),
                    "description": li.description,
                    "quantity": float(li.quantity) if li.quantity else None,
                    "unit_price": float(li.unit_price) if li.unit_price else None,
                    "total": float(li.total) if li.total else None,
                }
                for li in po.line_items
            ],
            "created_at": po.created_at.isoformat() if po.created_at else "",
        }
        for po in pos
    ]


@router.post("/sync-erp")
async def sync_pos_from_erp(
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Pull purchase orders from the connected ERP."""
    erp_config = (org.settings or {}).get("erp")
    if not erp_config:
        raise HTTPException(status_code=400, detail="No ERP configured")

    # Look up vendors by name for linking
    v_result = await db.execute(select(Vendor).where(Vendor.organization_id == org_id))
    vendor_map = {v.name.lower(): v.id for v in v_result.scalars().all()}

    # TODO: call ERP adapter to fetch real POs
    # For now, use mock data
    mock_pos = [
        {
            "po_number": "PO-2024-200",
            "vendor_name": "Office Supplies Co",
            "total": "2500.00",
            "status": "open",
            "lines": [
                {
                    "description": "Printer paper - bulk",
                    "quantity": "20",
                    "unit_price": "45.00",
                    "total": "900.00",
                },
                {
                    "description": "Ink cartridges",
                    "quantity": "10",
                    "unit_price": "80.00",
                    "total": "800.00",
                },
                {
                    "description": "Desk organizers",
                    "quantity": "16",
                    "unit_price": "50.00",
                    "total": "800.00",
                },
            ],
        },
        {
            "po_number": "PO-2024-201",
            "vendor_name": "Cloud Services Inc",
            "total": "15000.00",
            "status": "open",
            "lines": [
                {
                    "description": "Annual SaaS license",
                    "quantity": "1",
                    "unit_price": "12000.00",
                    "total": "12000.00",
                },
                {
                    "description": "Premium support addon",
                    "quantity": "1",
                    "unit_price": "3000.00",
                    "total": "3000.00",
                },
            ],
        },
        {
            "po_number": "PO-2024-202",
            "vendor_name": "Tech Hardware Corp",
            "total": "24000.00",
            "status": "open",
            "lines": [
                {
                    "description": "Laptop Model X Pro",
                    "quantity": "10",
                    "unit_price": "2400.00",
                    "total": "24000.00",
                },
            ],
        },
    ]

    created = 0
    skipped = 0
    for mock_po in mock_pos:
        # Check if PO already exists
        existing = await db.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.po_number == mock_po["po_number"],
                PurchaseOrder.organization_id == org_id,
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        vendor_id = vendor_map.get(mock_po["vendor_name"].lower())

        po = PurchaseOrder(
            po_number=mock_po["po_number"],
            vendor_id=vendor_id,
            total=Decimal(mock_po["total"]),
            status=mock_po["status"],
            organization_id=org_id,
        )
        db.add(po)
        await db.flush()

        for line in mock_po.get("lines", []):
            db.add(
                POLineItem(
                    po_id=po.id,
                    description=line.get("description"),
                    quantity=Decimal(line["quantity"]) if line.get("quantity") else None,
                    unit_price=Decimal(line["unit_price"]) if line.get("unit_price") else None,
                    total=Decimal(line["total"]) if line.get("total") else None,
                )
            )

        created += 1

    await db.commit()
    return {
        "success": True,
        "message": f"Synced {created} new POs, {skipped} already exist",
        "created": created,
        "skipped": skipped,
    }
