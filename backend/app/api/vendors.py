"""Vendor CRUD endpoints with verification workflow."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_org_id
from app.models.user import User
from app.models.organization import Organization
from app.tenant import get_tenant, get_tenant_db
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.schemas.vendor import VendorCreate, VendorResponse, VendorUpdate
from app.services.vendor_sync import sync_vendors_from_erp

router = APIRouter(prefix="/vendors", tags=["vendors"])


@router.get("")
async def list_vendors(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    source: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    query = select(Vendor)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Vendor.name.ilike(pattern) | Vendor.code.ilike(pattern) | Vendor.email.ilike(pattern)
        )
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(Vendor.status.in_(statuses))
    if source:
        query = query.where(Vendor.source == source)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Vendor.name).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    vendors = result.scalars().all()

    # Get invoice counts per vendor
    items = []
    for v in vendors:
        count_result = await db.execute(
            select(func.count()).where(Invoice.vendor_id == v.id)
        )
        inv_count = count_result.scalar() or 0
        items.append(VendorResponse.from_db(v, inv_count))

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{vendor_id}", response_model=VendorResponse)
async def get_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Vendor).where(Vendor.id == vendor_id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return VendorResponse.from_db(vendor)


@router.post("", response_model=VendorResponse, status_code=status.HTTP_201_CREATED)
async def create_vendor(
    body: VendorCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_org_id),
):
    vendor = Vendor(
        **body.model_dump(),
        organization_id=org_id,
        status="active",
        source="manual",
        verified_by=user.full_name,
        verified_at=datetime.now(timezone.utc),
    )
    db.add(vendor)
    await db.flush()
    await db.refresh(vendor)
    return VendorResponse.from_db(vendor)


@router.patch("/{vendor_id}", response_model=VendorResponse)
async def update_vendor(
    vendor_id: uuid.UUID,
    body: VendorUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Vendor).where(Vendor.id == vendor_id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(vendor, field, value)

    await db.flush()
    await db.refresh(vendor)
    return VendorResponse.from_db(vendor)


@router.delete("/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Vendor).where(Vendor.id == vendor_id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    await db.delete(vendor)
    await db.commit()


@router.post("/{vendor_id}/verify", response_model=VendorResponse)
async def verify_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Verify an unverified vendor — makes them eligible for payment."""
    result = await db.execute(
        select(Vendor).where(Vendor.id == vendor_id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    if vendor.status != "unverified":
        raise HTTPException(status_code=409, detail="Vendor is not in unverified status")

    vendor.status = "active"
    vendor.verified_by = user.full_name
    vendor.verified_at = datetime.now(timezone.utc)
    await db.commit()
    return VendorResponse.from_db(vendor)


@router.post("/{vendor_id}/reject", response_model=VendorResponse)
async def reject_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Reject an unverified vendor — marks as invalid/duplicate."""
    result = await db.execute(
        select(Vendor).where(Vendor.id == vendor_id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    if vendor.status not in ("unverified", "active"):
        raise HTTPException(status_code=409, detail="Vendor cannot be rejected from this status")

    vendor.status = "rejected"
    await db.commit()
    return VendorResponse.from_db(vendor)


@router.post("/sync-erp")
async def sync_vendors_from_erp_endpoint(
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Pull vendors from the connected ERP and sync to local database."""
    erp_config = (org.settings or {}).get("erp")
    if not erp_config:
        raise HTTPException(status_code=400, detail="No ERP configured. Set up ERP integration in Organization Settings.")

    # Use ERP adapter to fetch vendors
    import app.services.erp_adapters.mock_adapter  # noqa: F401
    import app.services.erp_adapters.merge_dev  # noqa: F401
    import app.services.erp_adapters.dynamics_365_bc  # noqa: F401
    import app.services.erp_adapters.netsuite  # noqa: F401
    from app.services.erp_adapters import get_erp_adapter

    adapter = get_erp_adapter(erp_config)

    # For now, use mock vendor data — real adapters would call adapter.list_vendors()
    # TODO: add list_vendors() to ErpAdapter interface
    mock_erp_vendors = [
        {
            "erp_vendor_id": "ERP-V001",
            "name": "Office Supplies Co",
            "code": "OSC",
            "email": "ap@officesupplies.com",
            "payment_terms": "Net 30",
        },
        {
            "erp_vendor_id": "ERP-V002",
            "name": "Cloud Services Inc",
            "code": "CSI",
            "email": "billing@cloudservices.com",
            "payment_terms": "Net 20",
        },
    ]

    result = await sync_vendors_from_erp(db, org_id, mock_erp_vendors)
    await db.commit()

    return {
        "success": True,
        "message": f"Synced {result['created']} new, {result['updated']} updated, {result['unchanged']} unchanged",
        **result,
    }
