"""Tenant resolution — maps X-Tenant-Slug header to a per-tenant DB session.

Also hosts the multi-entity (Phase 2) request scoping primitives:
``get_entity_id`` resolves the ``X-Entity-ID`` header to a validated
subsidiary (or ``None`` for the consolidated "all entities" view),
``get_write_entity_id`` picks the entity a newly-created row lands under, and
``apply_entity_scope`` filters a ``select()`` to one entity. Entities subdivide
data *within* a tenant DB — they are NOT the tenant boundary itself (that's
still the per-org DB resolved below). See ``docs/multi-entity.md``.
"""

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from app.database import get_control_db, get_tenant_engine
from app.models.entity import Entity
from app.models.organization import Organization

# Sentinel the entity switcher sends to ask for the consolidated view across
# every entity. An absent header means the same thing (so API consumers that
# predate multi-entity keep seeing all rows). Both resolve to ``None``.
ALL_ENTITIES = "all"


async def get_tenant_slug(
    x_tenant_slug: str | None = Header(default=None),
) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Tenant-Slug header",
        )
    return x_tenant_slug


async def get_tenant(
    slug: str = Depends(get_tenant_slug),
    db: AsyncSession = Depends(get_control_db),
    authorization: str | None = Header(default=None),
) -> Organization:
    """Resolve the Organization for the requested tenant.

    Cross-tenant guard: if the caller presents an employee JWT (typ
    other than ``vendor``), the token's ``org`` claim must match the
    resolved tenant. Otherwise the X-Tenant-Slug header alone decides
    which tenant's row the endpoint sees — letting any authenticated
    user from tenant A read or mutate tenant B's data by swapping the
    header.

    Vendor-portal tokens are exempt: VendorUser rows live in the
    per-tenant DB, so a cross-tenant attempt fails naturally on the
    user-lookup query in ``get_current_vendor_user``. Unauthenticated
    requests are also exempt — the downstream auth dependency will
    reject them with 401 before any data is read.

    The check lives here (not in ``get_tenant_db``) so it covers
    every endpoint that pulls the Organization object directly, not
    only the ones that also open a per-tenant DB session.
    """
    result = await db.execute(select(Organization).where(Organization.slug == slug))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown tenant: {slug}",
        )

    if authorization and authorization.startswith("Bearer "):
        # Local import avoids a circular dependency with `app.api.deps`,
        # which itself imports from `app.tenant`.
        from app.api.deps import decode_token

        try:
            payload = decode_token(authorization.removeprefix("Bearer "))
        except HTTPException:
            payload = None

        if payload is not None and payload.get("typ") != "vendor":
            token_org = payload.get("org")
            if token_org and str(org.id) != token_org:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Token does not match the requested tenant",
                )

    return org


async def get_tenant_db(
    tenant: Organization = Depends(get_tenant),
) -> AsyncGenerator[AsyncSession]:
    """Yield a SQLAlchemy session bound to the tenant's DB.

    The cross-tenant guard lives in ``get_tenant``; by the time we
    reach here, the JWT's org claim has already been validated
    against the requested tenant.
    """
    engine = get_tenant_engine(tenant.db_name)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Multi-entity (Phase 2) — request-scoped entity selection
# ---------------------------------------------------------------------------


async def get_entity_id(
    x_entity_id: str | None = Header(default=None),
    db: AsyncSession = Depends(get_tenant_db),
) -> uuid.UUID | None:
    """Resolve the ``X-Entity-ID`` header to a validated entity, or ``None``.

    ``None`` (the consolidated "all entities" view) is returned when the header
    is absent — so a client that never learned about multi-entity keeps seeing
    every entity's rows — or when it is the literal ``all``.

    A UUID is validated against *this tenant's* ``entities`` table. The table is
    tenant-local, so an id that doesn't exist here can't widen the scope or
    point at another tenant's subsidiary: an unknown id is a 400, never a
    silent fall-through to "all". The dependency reuses the same tenant session
    as the endpoint (FastAPI caches ``get_tenant_db`` within a request), so this
    costs one indexed lookup.
    """
    if x_entity_id is None:
        return None
    raw = x_entity_id.strip()
    if raw.lower() == ALL_ENTITIES:
        return None
    try:
        entity_uuid = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid X-Entity-ID header"
        )
    exists = (
        await db.execute(select(Entity.id).where(Entity.id == entity_uuid))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown entity for this tenant",
        )
    return entity_uuid


async def resolve_default_entity_id(db: AsyncSession) -> uuid.UUID:
    """Return the tenant's single ``is_default`` entity id.

    Every tenant is provisioned with exactly one default entity (migration
    0029 / ``tenant_provisioning``), so this is the home for rows created while
    the consolidated view is selected. A missing default means the tenant was
    provisioned incorrectly — surface it loudly rather than writing a NULL.
    """
    default_id = (await db.execute(select(Entity.id).where(Entity.is_default))).scalar_one_or_none()
    if default_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tenant has no default entity",
        )
    return default_id


async def get_write_entity_id(
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> uuid.UUID:
    """The entity a newly-created row should belong to.

    When the caller has an entity selected, new rows land under it. When the
    consolidated view is active (``None``), they land under the tenant's
    default entity — never NULL, so a freshly-created invoice is always visible
    in some entity-scoped view (a NULL ``entity_id`` would vanish from every
    per-entity list while showing only in the consolidated one).

    ``GLAccount`` is the exception and does NOT use this — a NULL there means
    "shared chart across all entities", which is the intended default for new
    accounts; that endpoint assigns ``entity_id`` explicitly.
    """
    if entity_id is not None:
        return entity_id
    return await resolve_default_entity_id(db)


def apply_entity_scope(
    query: Select,
    model: type,
    entity_id: uuid.UUID | None,
    *,
    include_shared: bool = False,
) -> Select:
    """Filter ``query`` to a single entity when one is selected.

    ``entity_id is None`` (consolidated view) returns the query untouched — the
    endpoint sees every entity's rows. Otherwise the query is narrowed to
    ``model.entity_id == entity_id``.

    ``include_shared=True`` also admits rows with a NULL ``entity_id``. This is
    only for the chart of accounts (``GLAccount``), where NULL means "shared
    across every entity", so an entity's effective chart is ``shared ∪ its
    own``. Every other table backfills to the default entity and never carries a
    meaningful NULL, so the default (``False``) is correct for them.
    """
    if entity_id is None:
        return query
    col = model.entity_id
    if include_shared:
        return query.where(or_(col == entity_id, col.is_(None)))
    return query.where(col == entity_id)
