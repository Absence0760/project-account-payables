"""Tenant resolution — maps X-Tenant-Slug header to a per-tenant DB session."""

from collections.abc import AsyncGenerator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import get_control_db, get_tenant_engine
from app.models.organization import Organization


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
