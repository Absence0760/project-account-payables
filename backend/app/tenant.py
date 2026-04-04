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
) -> Organization:
    result = await db.execute(
        select(Organization).where(Organization.slug == slug)
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown tenant: {slug}",
        )
    return org


async def get_tenant_db(
    tenant: Organization = Depends(get_tenant),
) -> AsyncGenerator[AsyncSession]:
    engine = get_tenant_engine(tenant.db_name)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
