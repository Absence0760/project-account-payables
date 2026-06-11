"""Vendor CRUD endpoints with verification workflow + portal-user management."""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.api.pagination import PaginationParams, paginated, pagination_params
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser
from app.schemas.portal import (
    PortalInviteRequest,
    PortalInviteResponse,
    PortalUserResponse,
)
from app.schemas.vendor import VendorCreate, VendorResponse, VendorUpdate
from app.services.csv_import import import_vendors_csv
from app.services.email_adapters import EmailMessage, get_email_adapter
from app.services.vendor_sync import sync_vendors_from_erp
from app.tenant import get_tenant, get_tenant_db
from app.utils.passwords import generate_temp_password
from app.utils.passwords import pwd_context as _pwd

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vendors", tags=["vendors"])


def _merge_bank_details(existing: dict | None, incoming: dict | None) -> dict | None:
    """Merge an incoming `bank_details` partial into the stored JSONB.

    The column has historically held arbitrary processor metadata, so a
    PATCH that only sets `counterparty_id` must not clobber sibling
    keys. Empty string and ``None`` are treated as "clear this key" so
    the UI can remove a counterparty without inventing a magic value.
    A fully-cleared dict is collapsed back to ``None``.
    """
    merged = dict(existing or {})
    for k, v in (incoming or {}).items():
        if v is None or v == "":
            merged.pop(k, None)
        else:
            merged[k] = v
    return merged or None


@router.get("")
async def list_vendors(
    pagination: PaginationParams = Depends(pagination_params),
    search: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    source: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
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

    query = query.order_by(Vendor.name).offset(pagination.offset).limit(pagination.limit)
    result = await db.execute(query)
    vendors = result.scalars().all()

    # Get invoice counts per vendor
    items = []
    for v in vendors:
        count_result = await db.execute(select(func.count()).where(Invoice.vendor_id == v.id))
        inv_count = count_result.scalar() or 0
        items.append(VendorResponse.from_db(v, inv_count))

    return paginated(items, total, pagination)


@router.get("/{vendor_id}", response_model=VendorResponse)
async def get_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return VendorResponse.from_db(vendor)


@router.post("", response_model=VendorResponse, status_code=status.HTTP_201_CREATED)
async def create_vendor(
    body: VendorCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    vendor = Vendor(
        **body.model_dump(),
        organization_id=org_id,
        status="active",
        source="manual",
        verified_by=user.full_name,
        verified_at=datetime.now(UTC),
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
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    payload = body.model_dump(exclude_unset=True)

    if "bank_details" in payload:
        vendor.bank_details = _merge_bank_details(vendor.bank_details, payload.pop("bank_details"))

    for field, value in payload.items():
        setattr(vendor, field, value)

    await db.flush()
    await db.refresh(vendor)
    return VendorResponse.from_db(vendor)


@router.delete("/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    await db.delete(vendor)
    await db.commit()


@router.post("/{vendor_id}/verify", response_model=VendorResponse)
async def verify_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Verify an unverified vendor — makes them eligible for payment."""
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    if vendor.status != "unverified":
        raise HTTPException(status_code=409, detail="Vendor is not in unverified status")

    vendor.status = "active"
    vendor.verified_by = user.full_name
    vendor.verified_at = datetime.now(UTC)
    await db.commit()
    return VendorResponse.from_db(vendor)


@router.post("/{vendor_id}/reject", response_model=VendorResponse)
async def reject_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Reject an unverified vendor — marks as invalid/duplicate."""
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
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
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Pull vendors from the connected ERP and sync to local database."""
    erp_config = (org.settings or {}).get("erp")
    if not erp_config:
        raise HTTPException(
            status_code=400,
            detail="No ERP configured. Set up ERP integration in Organization Settings.",
        )

    # Use ERP adapter to fetch vendors
    import app.services.erp_adapters.dynamics_365_bc  # noqa: F401
    import app.services.erp_adapters.merge_dev  # noqa: F401
    import app.services.erp_adapters.mock_adapter  # noqa: F401
    import app.services.erp_adapters.netsuite  # noqa: F401
    from app.services.erp_adapters import get_erp_adapter

    get_erp_adapter(erp_config)

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
        "message": (
            f"Synced {result['created']} new, "
            f"{result['updated']} updated, "
            f"{result['unchanged']} unchanged"
        ),
        **result,
    }


@router.post("/import-csv", status_code=status.HTTP_200_OK)
async def import_vendors_from_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Bulk-create vendors from a CSV export.

    Columns (case-insensitive, order-free): ``name`` (required), ``code``,
    ``email``, ``phone``, ``address``, ``tax_id``, ``payment_terms``,
    ``accepts_virtual_cards``. Duplicate detection uses ``code`` first, then
    case-insensitive ``name``. See ``backend/docs/csv-import.md``.
    """
    raw = await file.read()
    try:
        csv_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded") from None

    result = await import_vendors_csv(db, org_id, csv_text)
    await db.commit()
    return result.to_dict()


# ---------- Supplier-portal user management ----------


def _vendor_user_response(vu: VendorUser) -> PortalUserResponse:
    return PortalUserResponse(
        id=str(vu.id),
        vendor_id=str(vu.vendor_id),
        email=vu.email,
        full_name=vu.full_name,
        is_active=vu.is_active,
        must_change_password=vu.must_change_password,
        last_login_at=vu.last_login_at,
        created_at=vu.created_at,
    )


async def _get_vendor_or_404(db: AsyncSession, vendor_id: uuid.UUID) -> Vendor:
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


@router.get("/{vendor_id}/portal-users", response_model=list[PortalUserResponse])
async def list_vendor_portal_users(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    await _get_vendor_or_404(db, vendor_id)
    rows = (
        (
            await db.execute(
                select(VendorUser)
                .where(VendorUser.vendor_id == vendor_id)
                .order_by(VendorUser.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_vendor_user_response(vu) for vu in rows]


@router.post(
    "/{vendor_id}/portal-users",
    response_model=PortalInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_vendor_portal_user(
    vendor_id: uuid.UUID,
    body: PortalInviteRequest,
    org: Organization = Depends(get_tenant),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Create a supplier-portal user for a vendor and email them a temp
    password. Idempotent-ish: second invite for the same email is rejected
    (409) so we don't silently overwrite a working credential."""
    vendor = await _get_vendor_or_404(db, vendor_id)

    existing = (
        await db.execute(select(VendorUser).where(VendorUser.email == body.email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="A portal user with this email already exists.")

    temp_password = generate_temp_password()
    vu = VendorUser(
        vendor_id=vendor.id,
        email=body.email,
        full_name=body.full_name,
        hashed_password=_pwd.hash(temp_password),
        is_active=True,
        must_change_password=True,
    )
    db.add(vu)
    await db.flush()
    await db.refresh(vu)

    # Best-effort welcome email. If delivery fails we still return 201 with
    # `temp_password` so the admin can share it manually — same pattern as
    # the tenant-signup welcome email.
    email_adapter = get_email_adapter()
    portal_url = f"https://{org.slug}.app.com/portal"
    try:
        await email_adapter.send(
            EmailMessage(
                to=body.email,
                subject=f"You've been invited to {org.name}'s supplier portal",
                body_text=(
                    f"Hi {body.full_name},\n\n"
                    f"{org.name} has set up a supplier-portal account for "
                    f"{vendor.name}. Use it to submit invoices and track "
                    f"payment status.\n\n"
                    f"  URL:      {portal_url}\n"
                    f"  Email:    {body.email}\n"
                    f"  Password: {temp_password}\n\n"
                    "You'll be asked to change your password on first sign-in.\n"
                ),
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Portal-user welcome email failed for %s (vendor=%s)", body.email, vendor.id
        )

    return PortalInviteResponse(user=_vendor_user_response(vu), temp_password=temp_password)


@router.delete(
    "/{vendor_id}/portal-users/{vendor_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_vendor_portal_user(
    vendor_id: uuid.UUID,
    vendor_user_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    result = await db.execute(
        select(VendorUser).where(
            VendorUser.id == vendor_user_id,
            VendorUser.vendor_id == vendor_id,
        )
    )
    vu = result.scalar_one_or_none()
    if not vu:
        raise HTTPException(status_code=404, detail="Portal user not found")
    await db.delete(vu)
    await db.commit()
