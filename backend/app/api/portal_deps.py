"""Supplier-portal auth dependency.

Lives outside `deps.py` because it pulls a tenant-scoped session; keeping
the two dependency trees separate means a refactor of employee auth can't
silently change vendor-auth behavior (or vice versa).
"""

import uuid

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import decode_token
from app.models.organization import Organization
from app.models.vendor_user import VendorUser
from app.redis import is_token_blocked
from app.tenant import get_tenant, get_tenant_db


async def get_current_vendor_user(
    authorization: str | None = Header(default=None),
    tenant: Organization = Depends(get_tenant),
    db: AsyncSession = Depends(get_tenant_db),
) -> VendorUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = authorization.removeprefix("Bearer ")
    payload = decode_token(token)

    if payload.get("typ") != "vendor":
        # An employee token must not pass through the portal.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        vu_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    jti = payload.get("jti")
    if jti and await is_token_blocked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked"
        )

    result = await db.execute(select(VendorUser).where(VendorUser.id == vu_id))
    vu = result.scalar_one_or_none()
    if vu is None or not vu.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    # Positive tenant-boundary check: the portal user's recorded organization_id
    # must match the resolved tenant. The `vendor_users` row already lives in one
    # tenant DB, but this makes the binding explicit rather than resting on a
    # UUID-collision assumption — a row whose org doesn't match the requested
    # tenant can't authenticate. NULL is tolerated only for un-backfilled legacy
    # rows (the additive migration backfills every existing row); a populated
    # mismatch is a hard 401 (opaque "User not found" — no enumeration).
    if vu.organization_id is not None and vu.organization_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return vu
