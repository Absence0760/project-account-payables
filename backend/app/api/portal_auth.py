"""Supplier-portal auth — login, logout, /me, change-password.

Completely separate from `/api/auth/*` (employee auth). Vendor users live in
the tenant DB; the tenant is resolved from the `X-Tenant-Slug` header, same
as every other tenant-scoped route.
"""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import create_vendor_access_token, decode_token
from app.api.portal_deps import get_current_vendor_user
from app.config import settings
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser
from app.redis import block_token
from app.schemas.portal import (
    PortalChangePasswordRequest,
    PortalLoginRequest,
    PortalMeResponse,
    PortalMFAChallengeResponse,
    PortalMFAChallengeVerifyRequest,
    PortalMFADisableRequest,
    PortalMFAEnrollStartResponse,
    PortalMFAVerifyRequest,
    PortalTokenResponse,
)
from app.services import mfa
from app.services.rate_limit import check_rate_limit
from app.tenant import get_tenant_db
from app.utils.passwords import (
    PasswordError,
    pwd_context,
    validate_password_complexity,
)

router = APIRouter(prefix="/portal/auth", tags=["portal-auth"])


@router.post("/login")
async def portal_login(
    body: PortalLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> PortalTokenResponse | PortalMFAChallengeResponse:
    """Exchange email+password for a portal access token. The `X-Tenant-Slug`
    header scopes the lookup — a vendor user in one tenant cannot authenticate
    against another tenant's portal.

    When the vendor has MFA enrolled (and the `AP_MFA_ENABLED` master switch is
    on), returns a short-lived `PortalMFAChallengeResponse` instead of the
    access token; the browser then completes `/portal/auth/mfa/challenge`."""
    await check_rate_limit("portal_auth_login", request, limit=10, window_seconds=60)
    result = await db.execute(select(VendorUser).where(VendorUser.email == body.email))
    vu = result.scalar_one_or_none()

    if not vu or not vu.hashed_password or not vu.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not pwd_context.verify(body.password, vu.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    vu.last_login_at = datetime.now(UTC)
    await db.commit()

    # MFA gate. The master switch (`AP_MFA_ENABLED`) wins — if MFA is off at the
    # platform level we skip even an enrolled vendor, keeping local-dev login
    # painless. MFA is opt-in per vendor user (no org-wide enforcement yet).
    if settings.mfa_enabled and vu.mfa_enabled and vu.mfa_secret:
        challenge_token = mfa.create_vendor_challenge_token(vu.id)
        return PortalMFAChallengeResponse(mfa_challenge_token=challenge_token)

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
        mfa_enabled=vu.mfa_enabled,
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
        mfa_enabled=vu.mfa_enabled,
        vendor_id=str(vu.vendor_id),
        vendor_name=vendor.name if vendor else "",
        vendor_status=vendor.status if vendor else "unknown",
    )


# ---------------------------------------------------------------------------
# MFA (TOTP) — challenge verify + per-vendor enrollment management
# ---------------------------------------------------------------------------


@router.post("/mfa/challenge")
async def portal_mfa_challenge(
    body: PortalMFAChallengeVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> PortalTokenResponse:
    """Trade the login-issued challenge token + a valid TOTP code for a real
    vendor access token. Public-by-design (the challenge token proves the
    password was already accepted), so it mirrors `/login`'s rate limiting."""
    # TOTP is a 6-digit code (10^6 keyspace). 10 attempts / minute caps an
    # online brute-force well inside the short challenge TTL.
    await check_rate_limit("portal_auth_mfa_verify", request, limit=10, window_seconds=60)
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled")
    try:
        vu_id = mfa.decode_vendor_challenge_token(body.challenge_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    vu = (await db.execute(select(VendorUser).where(VendorUser.id == vu_id))).scalar_one_or_none()
    if not vu or not vu.is_active:
        raise HTTPException(status_code=401, detail="Invalid challenge")
    if not vu.mfa_enabled or not vu.mfa_secret:
        raise HTTPException(status_code=400, detail="MFA not enrolled for this account")
    if not mfa.verify_totp(vu.mfa_secret, body.code):
        raise HTTPException(status_code=401, detail="Invalid code")

    token = create_vendor_access_token(vu.id, vu.vendor_id)
    return PortalTokenResponse(
        access_token=token,
        must_change_password=vu.must_change_password,
    )


@router.post("/mfa/enroll", response_model=PortalMFAEnrollStartResponse)
async def portal_mfa_enroll(
    vu: VendorUser = Depends(get_current_vendor_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Start TOTP enrollment — mint (or re-issue) the secret + QR. The secret is
    held pending until `/mfa/verify` confirms the vendor can produce a code."""
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")

    secret = mfa.generate_totp_secret()
    vu.mfa_secret = secret
    vu.mfa_enabled = False  # not active until verified
    vu.mfa_enrolled_at = None
    await db.commit()

    uri = mfa.provisioning_uri(secret, account_label=vu.email)
    return PortalMFAEnrollStartResponse(
        secret=secret,
        provisioning_uri=uri,
        qr_code_data_url=mfa.qr_code_data_url(uri),
    )


@router.post("/mfa/verify", response_model=PortalMeResponse)
async def portal_mfa_verify(
    body: PortalMFAVerifyRequest,
    vu: VendorUser = Depends(get_current_vendor_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Confirm the vendor can produce a valid code, then flip MFA on."""
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")
    if not vu.mfa_secret:
        raise HTTPException(status_code=400, detail="Start enrollment first")
    if not mfa.verify_totp(vu.mfa_secret, body.code):
        raise HTTPException(status_code=401, detail="Invalid code")

    vu.mfa_enabled = True
    vu.mfa_enrolled_at = datetime.now(UTC)
    await db.commit()

    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    return PortalMeResponse(
        id=str(vu.id),
        email=vu.email,
        full_name=vu.full_name,
        must_change_password=vu.must_change_password,
        mfa_enabled=vu.mfa_enabled,
        vendor_id=str(vu.vendor_id),
        vendor_name=vendor.name if vendor else "",
        vendor_status=vendor.status if vendor else "unknown",
    )


@router.post("/mfa/disable", response_model=PortalMeResponse)
async def portal_mfa_disable(
    body: PortalMFADisableRequest,
    vu: VendorUser = Depends(get_current_vendor_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Turn off TOTP for this vendor account. Requires a valid current code —
    a stolen session shouldn't be able to silently strip MFA off."""
    if not vu.mfa_enabled or not vu.mfa_secret:
        raise HTTPException(status_code=400, detail="MFA is not enabled")
    if not mfa.verify_totp(vu.mfa_secret, body.code):
        raise HTTPException(status_code=401, detail="Invalid code")

    vu.mfa_secret = None
    vu.mfa_enabled = False
    vu.mfa_enrolled_at = None
    await db.commit()

    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    return PortalMeResponse(
        id=str(vu.id),
        email=vu.email,
        full_name=vu.full_name,
        must_change_password=vu.must_change_password,
        mfa_enabled=vu.mfa_enabled,
        vendor_id=str(vu.vendor_id),
        vendor_name=vendor.name if vendor else "",
        vendor_status=vendor.status if vendor else "unknown",
    )
