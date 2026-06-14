"""Procurement / Requisitions — catalogs + guided-buying router.

Catalog management (supplier catalogs + punch-out) and guided buying (steer
buyers to preferred vendors / contracts / catalog lines). The data model
(``app/models/procurement.py`` — ``Catalog`` / ``CatalogItem`` / ``CatalogType``)
and migration ``0041_procurement`` are shipped; this router implements the
vertical on top of them.

RBAC: catalogs are configuration-like (mirrors vendors). Read =
admin/ap_manager/ap_clerk/cfo; mutate = admin/ap_manager. Guided-buying
suggestions: read = admin/ap_manager/ap_clerk/cfo. Every mutation writes a
``dispatch_audit`` row; money is ``Decimal`` in / ``float`` out.

Punch-out is config-only: the catalog stores ``punchout_url`` (and is typed
``punchout``); live cXML/OCI punch-out round-trips are a future extension. See
``backend/docs/procurement-catalogs.md``.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.api.pagination import PaginationParams, pagination_params
from app.models.gl_account import GLAccount
from app.models.procurement import Catalog, CatalogItem, CatalogType
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.catalog import (
    CatalogCreate,
    CatalogItemCreate,
    CatalogItemResponse,
    CatalogItemUpdate,
    CatalogListResponse,
    CatalogResponse,
    CatalogUpdate,
    GuidedBuyingSuggestion,
)
from app.services.audit_dispatch import dispatch_audit
from app.services.catalog_service import build_guided_buying_suggestion
from app.tenant import (
    apply_entity_scope,
    get_entity_id,
    get_tenant_db,
    get_write_entity_id,
)

router = APIRouter(prefix="/catalogs", tags=["catalogs"])

# Fields a PATCH on a catalog may touch.
_CATALOG_UPDATABLE_FIELDS = (
    "name",
    "catalog_type",
    "punchout_url",
    "is_active",
    "is_preferred",
    "description",
)
# Plain scalar fields a PATCH on a catalog item may touch (FK fields handled
# explicitly because they need UUID coercion + existence checks).
_ITEM_UPDATABLE_FIELDS = (
    "sku",
    "name",
    "description",
    "unit_price",
    "currency",
    "uom",
    "category",
    "is_active",
)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _item_to_response(i: CatalogItem) -> CatalogItemResponse:
    return CatalogItemResponse(
        id=str(i.id),
        catalog_id=str(i.catalog_id),
        sku=i.sku,
        name=i.name,
        description=i.description,
        unit_price=float(i.unit_price) if i.unit_price is not None else None,
        currency=i.currency,
        uom=i.uom,
        vendor_id=str(i.vendor_id) if i.vendor_id else None,
        gl_account_id=str(i.gl_account_id) if i.gl_account_id else None,
        category=i.category,
        is_active=i.is_active,
        created_at=i.created_at.isoformat() if i.created_at else "",
        updated_at=i.updated_at.isoformat() if i.updated_at else "",
    )


def _to_response(c: Catalog, *, with_items: bool = False) -> CatalogResponse:
    items = sorted(c.items, key=lambda x: x.name or "") if with_items else []
    return CatalogResponse(
        id=str(c.id),
        name=c.name,
        catalog_type=str(c.catalog_type),
        vendor_id=str(c.vendor_id) if c.vendor_id else None,
        punchout_url=c.punchout_url,
        is_active=c.is_active,
        is_preferred=c.is_preferred,
        description=c.description,
        item_count=len(c.items),
        items=[_item_to_response(i) for i in items],
        created_at=c.created_at.isoformat() if c.created_at else "",
        updated_at=c.updated_at.isoformat() if c.updated_at else "",
    )


async def _get_catalog_or_404(db: AsyncSession, catalog_id: uuid.UUID) -> Catalog:
    catalog = (
        await db.execute(
            select(Catalog).where(Catalog.id == catalog_id).options(selectinload(Catalog.items))
        )
    ).scalar_one_or_none()
    if not catalog:
        raise HTTPException(status_code=404, detail="Catalog not found")
    return catalog


async def _get_item_or_404(db: AsyncSession, item_id: uuid.UUID) -> CatalogItem:
    item = (
        await db.execute(select(CatalogItem).where(CatalogItem.id == item_id))
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    return item


async def _resolve_vendor_id(
    db: AsyncSession, raw: str | None, org_id: uuid.UUID
) -> uuid.UUID | None:
    """Coerce + validate an optional vendor_id (tenant-local). ``None`` clears it."""
    if not raw:
        return None
    try:
        vendor_uuid = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid vendor_id")
    exists = (
        await db.execute(
            select(Vendor.id).where(Vendor.id == vendor_uuid, Vendor.organization_id == org_id)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor_uuid


async def _resolve_gl_id(db: AsyncSession, raw: str | None, org_id: uuid.UUID) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        gl_uuid = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid gl_account_id")
    exists = (
        await db.execute(
            select(GLAccount.id).where(GLAccount.id == gl_uuid, GLAccount.organization_id == org_id)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="GL account not found")
    return gl_uuid


# ---------------------------------------------------------------------------
# Catalogs — list + create
# ---------------------------------------------------------------------------


@router.get("", response_model=CatalogListResponse)
async def list_catalogs(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
    catalog_type: CatalogType | None = Query(None),
    is_active: bool | None = Query(None),
    is_preferred: bool | None = Query(None),
    search: str | None = Query(None),
    pagination: PaginationParams = Depends(pagination_params),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    base = apply_entity_scope(select(Catalog), Catalog, entity_id)
    if catalog_type is not None:
        base = base.where(Catalog.catalog_type == catalog_type)
    if is_active is not None:
        base = base.where(Catalog.is_active.is_(is_active))
    if is_preferred is not None:
        base = base.where(Catalog.is_preferred.is_(is_preferred))
    if search:
        base = base.where(Catalog.name.ilike(f"%{search.strip()}%"))

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0)
    paged = (
        base.options(selectinload(Catalog.items))
        .order_by(Catalog.is_preferred.desc(), Catalog.name)
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = (await db.execute(paged)).scalars().all()
    return CatalogListResponse(
        items=[_to_response(c) for c in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("", response_model=CatalogResponse, status_code=status.HTTP_201_CREATED)
async def create_catalog(
    body: CatalogCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    vendor_uuid = await _resolve_vendor_id(db, body.vendor_id, org_id)

    catalog = Catalog(
        name=body.name,
        catalog_type=body.catalog_type,
        vendor_id=vendor_uuid,
        punchout_url=body.punchout_url,
        is_active=body.is_active,
        is_preferred=body.is_preferred,
        description=body.description,
        organization_id=org_id,
        entity_id=entity_id,
    )
    db.add(catalog)
    await db.flush()
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="catalog.created",
        entity_type="catalog",
        entity_id=catalog.id,
        details={
            "name": catalog.name,
            "catalog_type": str(catalog.catalog_type),
            "is_preferred": catalog.is_preferred,
        },
    )
    await db.commit()
    fresh = await _get_catalog_or_404(db, catalog.id)
    return _to_response(fresh, with_items=True)


# ---------------------------------------------------------------------------
# Guided buying — literal segment declared BEFORE /{catalog_id} so it isn't
# captured as a UUID path param.
# ---------------------------------------------------------------------------


@router.get("/guided-buying", response_model=GuidedBuyingSuggestion)
async def guided_buying(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
    category: str | None = Query(None),
    vendor_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Steer a buyer toward preferred sources for the given criteria.

    Returns preferred vendors (own an active, preferred catalog), in-contract
    vendors (have an active contract), and matching active catalog items
    (preferred catalogs ranked first). Read-only and deterministic — no LLM."""
    return await build_guided_buying_suggestion(
        db,
        entity_id=entity_id,
        category=category,
        vendor_id=vendor_id,
        q=q,
    )


# ---------------------------------------------------------------------------
# Catalog item PATCH / DELETE — declared under the literal `items` prefix and
# BEFORE /{catalog_id} so `items` is never captured as a {catalog_id} UUID.
# ---------------------------------------------------------------------------


@router.patch("/items/{item_id}", response_model=CatalogItemResponse)
async def update_item(
    item_id: uuid.UUID,
    body: CatalogItemUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    item = await _get_item_or_404(db, item_id)
    payload = body.model_dump(exclude_unset=True)

    changed: list[str] = []
    if "vendor_id" in payload:
        new_vendor = await _resolve_vendor_id(db, payload.pop("vendor_id"), org_id)
        if item.vendor_id != new_vendor:
            item.vendor_id = new_vendor
            changed.append("vendor_id")
    if "gl_account_id" in payload:
        new_gl = await _resolve_gl_id(db, payload.pop("gl_account_id"), org_id)
        if item.gl_account_id != new_gl:
            item.gl_account_id = new_gl
            changed.append("gl_account_id")

    for field in _ITEM_UPDATABLE_FIELDS:
        if field in payload and getattr(item, field) != payload[field]:
            setattr(item, field, payload[field])
            changed.append(field)

    if changed:
        await dispatch_audit(
            db,
            correlation_id=uuid.uuid4(),
            organization_id=org_id,
            actor_id=user.id,
            action="catalog_item.updated",
            entity_type="catalog_item",
            entity_id=item.id,
            details={"fields": changed},
        )
    await db.commit()
    fresh = await _get_item_or_404(db, item.id)
    return _item_to_response(fresh)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    item = await _get_item_or_404(db, item_id)
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="catalog_item.deleted",
        entity_type="catalog_item",
        entity_id=item.id,
        details={"name": item.name, "catalog_id": str(item.catalog_id)},
    )
    await db.delete(item)
    await db.commit()


# ---------------------------------------------------------------------------
# Catalogs — get / patch / delete
# ---------------------------------------------------------------------------


@router.get("/{catalog_id}", response_model=CatalogResponse)
async def get_catalog(
    catalog_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
):
    return _to_response(await _get_catalog_or_404(db, catalog_id), with_items=True)


@router.patch("/{catalog_id}", response_model=CatalogResponse)
async def update_catalog(
    catalog_id: uuid.UUID,
    body: CatalogUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    catalog = await _get_catalog_or_404(db, catalog_id)
    payload = body.model_dump(exclude_unset=True)

    changed: list[str] = []
    if "vendor_id" in payload:
        new_vendor = await _resolve_vendor_id(db, payload.pop("vendor_id"), org_id)
        if catalog.vendor_id != new_vendor:
            catalog.vendor_id = new_vendor
            changed.append("vendor_id")

    for field in _CATALOG_UPDATABLE_FIELDS:
        if field in payload and getattr(catalog, field) != payload[field]:
            setattr(catalog, field, payload[field])
            changed.append(field)

    if changed:
        await dispatch_audit(
            db,
            correlation_id=uuid.uuid4(),
            organization_id=org_id,
            actor_id=user.id,
            action="catalog.updated",
            entity_type="catalog",
            entity_id=catalog.id,
            details={"fields": changed},
        )
    await db.commit()
    fresh = await _get_catalog_or_404(db, catalog.id)
    return _to_response(fresh, with_items=True)


@router.delete("/{catalog_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_catalog(
    catalog_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    catalog = await _get_catalog_or_404(db, catalog_id)
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="catalog.deleted",
        entity_type="catalog",
        entity_id=catalog.id,
        details={"name": catalog.name},
    )
    # Items cascade via the ORM relationship (delete-orphan).
    await db.delete(catalog)
    await db.commit()


# ---------------------------------------------------------------------------
# Nested catalog items — list + create
# ---------------------------------------------------------------------------


@router.get("/{catalog_id}/items", response_model=list[CatalogItemResponse])
async def list_items(
    catalog_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
    is_active: bool | None = Query(None),
):
    await _get_catalog_or_404(db, catalog_id)  # 404 if the catalog is unknown
    query = select(CatalogItem).where(CatalogItem.catalog_id == catalog_id)
    if is_active is not None:
        query = query.where(CatalogItem.is_active.is_(is_active))
    query = query.order_by(CatalogItem.name)
    rows = (await db.execute(query)).scalars().all()
    return [_item_to_response(i) for i in rows]


@router.post(
    "/{catalog_id}/items",
    response_model=CatalogItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    catalog_id: uuid.UUID,
    body: CatalogItemCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    catalog = await _get_catalog_or_404(db, catalog_id)
    vendor_uuid = await _resolve_vendor_id(db, body.vendor_id, org_id)
    gl_uuid = await _resolve_gl_id(db, body.gl_account_id, org_id)

    item = CatalogItem(
        catalog_id=catalog.id,
        sku=body.sku,
        name=body.name,
        description=body.description,
        unit_price=body.unit_price,
        currency=body.currency,
        uom=body.uom,
        vendor_id=vendor_uuid,
        gl_account_id=gl_uuid,
        category=body.category,
        is_active=body.is_active,
        organization_id=org_id,
        # Item lives under its catalog's entity so per-entity scoping is consistent.
        entity_id=catalog.entity_id,
    )
    db.add(item)
    await db.flush()
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="catalog_item.created",
        entity_type="catalog_item",
        entity_id=item.id,
        details={"name": item.name, "catalog_id": str(catalog.id)},
    )
    await db.commit()
    fresh = await _get_item_or_404(db, item.id)
    return _item_to_response(fresh)
