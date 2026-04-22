"""Supplier-portal auth — login, logout, /me, change-password.

Completely separate from `/api/auth/*` (employee auth). Vendor users live in
the tenant DB; the tenant is resolved from the `X-Tenant-Slug` header, same
as every other tenant-scoped route.
"""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import create_vendor_access_token, decode_token
from app.api.portal_deps import get_current_vendor_user
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser
from app.redis import block_token
from app.schemas.portal import (
    PortalChangePasswordRequest,
    PortalLoginRequest,
    PortalMeResponse,
    PortalTokenResponse,
)
from app.tenant import get_tenant_db
from app.utils.passwords import PasswordError, validate_password_complexity

router = APIRouter(prefix="/portal/auth", tags=["portal-auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/login", response_model=PortalTokenResponse)
async def portal_login(
    body: PortalLoginRequest,
    db: AsyncSession = Depends(get_tenant_db),
):
    """Exchange email+password for a portal access token. The `X-Tenant-Slug`
    header scopes the lookup — a vendor user in one tenant cannot authenticate
    against another tenant's portal."""
    result = await db.execute(select(VendorUser).where(VendorUser.email == body.email))
    vu = result.scalar_one_or_none()

    if not vu or not vu.hashed_password or not vu.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not pwd_context.verify(body.password, vu.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    vu.last_login_at = datetime.now(UTC)
    await db.commit()

    token = create_vendor_access_token(vu.id, vu.vendor_id)
    return PortalTokenResponse(
        access_token=token,
        must_change_password=vu.must_change_password,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def portal_logout(authorization: str = Header()):
    token = authorization.removeprefix("Bearer ")
    payload = decode_token(token)
    jti = payload.get("jti")
    if jti:
        exp = payload.get("exp", 0)
        ttl = max(int(exp - time.time()), 1)
        await block_token(jti, ttl)


@router.get("/me", response_model=PortalMeResponse)
async def portal_me(
    vu: VendorUser = Depends(get_current_vendor_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    if not vendor:
        # Vendor deleted out from under the portal user — treat as broken session.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Vendor not found")
    return PortalMeResponse(
        id=str(vu.id),
        email=vu.email,
        full_name=vu.full_name,
        must_change_password=vu.must_change_password,
        vendor_id=str(vendor.id),
        vendor_name=vendor.name,
        vendor_status=vendor.status,
    )


@router.post("/change-password", response_model=PortalMeResponse)
async def portal_change_password(
    body: PortalChangePasswordRequest,
    vu: VendorUser = Depends(get_current_vendor_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    if not vu.hashed_password or not pwd_context.verify(body.current_password, vu.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    try:
        validate_password_complexity(body.new_password)
    except PasswordError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    vu.hashed_password = pwd_context.hash(body.new_password)
    vu.must_change_password = False
    await db.commit()

    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    return PortalMeResponse(
        id=str(vu.id),
        email=vu.email,
        full_name=vu.full_name,
        must_change_password=vu.must_change_password,
        vendor_id=str(vu.vendor_id),
        vendor_name=vendor.name if vendor else "",
        vendor_status=vendor.status if vendor else "unknown",
    )
