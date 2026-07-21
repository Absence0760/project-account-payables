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
    PortalMFAEmailChallengeRequest,
    PortalMFAEnrollStartResponse,
    PortalMFAVerifyRequest,
    PortalTokenResponse,
    PortalUpdateProfileRequest,
)
from app.services import mfa
from app.services.email_adapters import EmailMessage, get_email_adapter, is_supported_locale
from app.services.rate_limit import check_rate_limit
from app.tenant import get_tenant_db
from app.utils.passwords import (
    PasswordError,
    dummy_verify,
    pwd_context,
    validate_password_complexity,
)

router = APIRouter(prefix="/portal/auth", tags=["portal-auth"])


async def _send_vendor_email_otp(vu: VendorUser, code: str) -> None:
    """Deliver the email-OTP backup code to the vendor user's account address via
    the configured email adapter (console in dev). The code is the only sensitive
    field and is passed in the body only — never logged."""
    msg = EmailMessage(
        to=vu.email,
        subject="Your supplier portal sign-in code",
        body_text=(
            f"Hi {vu.full_name},\n\n"
            f"Your sign-in code is: {code}\n\n"
            f"It expires in {settings.mfa_email_otp_ttl_seconds // 60} minutes. "
            "If you didn't try to sign in, ignore this email and consider "
            "rotating your password."
        ),
    )
    adapter = get_email_adapter()
    await adapter.send(msg)


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
        # Equalize timing with the wrong-password path so the response time
        # doesn't reveal whether a vendor account exists (enumeration).
        dummy_verify()
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
    # Only a vendor-portal token may be revoked here. Without this guard the
    # endpoint accepted ANY JWT signed with AP_SECRET_KEY — including an employee
    # `typ=user` token — and added its jti to the shared Redis blocklist, letting
    # the public portal-logout route revoke an employee session. Mirror the
    # symmetric `typ` check `get_current_vendor_user` enforces on every other
    # portal route.
    if payload.get("typ") != "vendor":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
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
        locale=vu.locale,
    )


@router.patch("/me", response_model=PortalMeResponse)
async def portal_update_me(
    body: PortalUpdateProfileRequest,
    vu: VendorUser = Depends(get_current_vendor_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Update the authenticated supplier user's profile preferences.

    Currently only the email-language `locale`. The vendor sets their OWN
    preference (RBAC = the authenticated vendor user); a supported value sets
    it, an empty string clears it (→ English fallback), an unsupported value is
    rejected (422). The pref drives outbound supplier email copy only — never
    portal UI. See docs/notifications.md § Localized email.
    """
    if "locale" in body.model_fields_set:
        if not body.locale:
            vu.locale = None
        elif is_supported_locale(body.locale):
            vu.locale = body.locale
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported locale '{body.locale}'.",
            )
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
        locale=vu.locale,
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
        locale=vu.locale,
    )


# ---------------------------------------------------------------------------
# MFA (TOTP) — challenge verify + per-vendor enrollment management
# ---------------------------------------------------------------------------


@router.post("/mfa/challenge/email", status_code=status.HTTP_204_NO_CONTENT)
async def portal_request_email_otp(
    body: PortalMFAEmailChallengeRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
):
    """Generate + email a one-time backup code to an MFA-enrolled vendor user.
    The `challenge_token` from `/login` proves the password was already accepted,
    so we don't email codes to random people. Public-by-design (same gating as
    `/mfa/challenge`); 204 on every path so it doesn't enumerate accounts."""
    # Cap how fast a single IP can churn email-OTPs — protects the vendor's
    # inbox from being weaponised as a notification spammer.
    await check_rate_limit("portal_auth_mfa_email", request, limit=5, window_seconds=60)
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled")
    try:
        claims = await mfa.decode_vendor_challenge_token(body.challenge_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    vu = (
        await db.execute(select(VendorUser).where(VendorUser.id == claims.subject_id))
    ).scalar_one_or_none()
    # Only an enrolled, active vendor gets a code. Stay 204 either way so the
    # response doesn't reveal which accounts exist / are enrolled.
    if not vu or not vu.is_active or not vu.mfa_enabled:
        return

    code = await mfa.issue_vendor_email_otp(vu.id)
    await _send_vendor_email_otp(vu, code)


@router.post("/mfa/challenge")
async def portal_mfa_challenge(
    body: PortalMFAChallengeVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> PortalTokenResponse:
    """Trade the login-issued challenge token + a valid code for a real vendor
    access token. `method` picks the factor: `totp` (the enrolled authenticator,
    default) or `email` (the on-demand email-OTP backup). Public-by-design (the
    challenge token proves the password was already accepted), so it mirrors
    `/login`'s rate limiting."""
    # TOTP is a 6-digit code (10^6 keyspace). 10 attempts / minute caps an
    # online brute-force well inside the short challenge TTL. Email-OTP is
    # single-use, so the limit there only mitigates timing-attack probing.
    await check_rate_limit("portal_auth_mfa_verify", request, limit=10, window_seconds=60)
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled")
    try:
        claims = await mfa.decode_vendor_challenge_token(body.challenge_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    vu = (
        await db.execute(select(VendorUser).where(VendorUser.id == claims.subject_id))
    ).scalar_one_or_none()
    if not vu or not vu.is_active:
        raise HTTPException(status_code=401, detail="Invalid challenge")
    # Both factors require the vendor to have actually enrolled MFA — the
    # email-OTP is a *backup* to TOTP, not an independent enrollment path.
    if not vu.mfa_enabled or not vu.mfa_secret:
        raise HTTPException(status_code=400, detail="MFA not enrolled for this account")

    if body.method == "email":
        if not await mfa.verify_vendor_email_otp(vu.id, body.code):
            raise HTTPException(status_code=401, detail="Invalid or expired code")
    else:
        if not await mfa.verify_totp(vu.mfa_secret, body.code):
            raise HTTPException(status_code=401, detail="Invalid code")

    # Single-use: burn the challenge token now that the factor is verified so
    # it can't be replayed to mint a second session (issue #162).
    await mfa.consume_challenge_token(claims.jti)

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
    if not await mfa.verify_totp(vu.mfa_secret, body.code):
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
    if not await mfa.verify_totp(vu.mfa_secret, body.code):
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
