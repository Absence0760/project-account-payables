"""Supplier-portal auth — login, logout, /me, change-password.

Completely separate from `/api/auth/*` (employee auth). Vendor users live in
the tenant DB; the tenant is resolved from the `X-Tenant-Slug` header, same
as every other tenant-scoped route.
"""

import time
import uuid
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
    PortalMFAStepUpRequest,
    PortalMFAVerifyRequest,
    PortalTokenResponse,
    PortalUpdateProfileRequest,
)
from app.services import mfa
from app.services.audit_dispatch import dispatch_auth_audit
from app.services.email_adapters import EmailMessage, get_email_adapter, is_supported_locale
from app.services.rate_limit import (
    EMAIL_OTP_PER_ACCOUNT_PER_HOUR,
    LOGIN_FAILURE_LIMIT,
    LOGIN_FAILURE_WINDOW_SECONDS,
    MFA_FAILURE_LIMIT,
    MFA_FAILURE_WINDOW_SECONDS,
    auth_identity_key,
    check_auth_failures,
    check_rate_limit,
    clear_auth_failures,
    record_auth_failure,
    resolve_client_ip,
)
from app.services.session_management import (
    end_session,
    register_session,
    revoke_other_sessions,
)
from app.tenant import get_tenant_db, get_tenant_slug
from app.utils.passwords import (
    PasswordError,
    dummy_verify,
    hash_password,
    validate_password_complexity,
    verify_password,
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


async def _audit_portal_login_failure(vu: VendorUser, *, ip: str, reason: str) -> None:
    """Record a rejected supplier sign-in.

    ``reason`` is one of a fixed set of literals (`bad_password`,
    `no_password`, `inactive`) — machine-readable for the tenant's admins and
    never derived from anything the caller supplied.

    PII-lean by construction: the account is identified by id, so a supplier
    contact's address is not restated into the trail on every guess. Skipped
    for a legacy row with no `organization_id` (the dispatcher resolves the
    tenant DB from it); `dispatch_auth_audit` swallows its own failures, so
    this never breaks the request either way.

    Reached only once the account is known, which is the same shape the
    employee twin already ships: an unknown address has no org and so no
    tenant trail to write into. The enumeration guard that matters — identical
    status + detail, and `dummy_verify` equalising the bcrypt cost that
    dominates the response — is unaffected.
    """
    if not vu.organization_id:
        return
    await dispatch_auth_audit(
        organization_id=vu.organization_id,
        actor_id=None,
        action="portal.login.failure",
        entity_id=vu.id,
        details={"ip": ip, "reason": reason},
    )


async def _mint_portal_session(
    vu: VendorUser, request: Request, *, method: str
) -> PortalTokenResponse:
    """Mint a supplier-portal access token AND register it as a tracked session.

    Employee sign-ins have been tracked in Redis (`active_jtis:<user_id>`) since
    session management landed; the portal minted a bare JWT and tracked nothing.
    That made "sign the supplier out of their other devices" impossible to
    implement at all — which is why a portal password change used to leave every
    other session of that supplier authenticating with the old token until it
    expired. Tracking is what gives `revoke_other_sessions` something to revoke.

    Uses the same helper as the employee surface, so the vendor user also
    inherits the concurrent-session cap (`FEOH_MAX_CONCURRENT_SESSIONS`) — the
    oldest session is evicted onto the blocklist past it. Vendor-user and
    employee ids are both UUID4s in the same Redis keyspace, so a vendor's set
    can never collide with an employee's.
    """
    token = create_vendor_access_token(vu.id, vu.vendor_id)
    jti = decode_token(token).get("jti")
    if jti:
        await register_session(
            vu.id,
            jti,
            ip=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            method=method,
        )
    return PortalTokenResponse(
        access_token=token,
        must_change_password=vu.must_change_password,
    )


@router.post("/login")
async def portal_login(
    body: PortalLoginRequest,
    request: Request,
    slug: str = Depends(get_tenant_slug),
    db: AsyncSession = Depends(get_tenant_db),
) -> PortalTokenResponse | PortalMFAChallengeResponse:
    """Exchange email+password for a portal access token. The `X-Tenant-Slug`
    header scopes the lookup — a vendor user in one tenant cannot authenticate
    against another tenant's portal.

    When the vendor has MFA enrolled (and the `FEOH_MFA_ENABLED` master switch is
    on), returns a short-lived `PortalMFAChallengeResponse` instead of the
    access token; the browser then completes `/portal/auth/mfa/challenge`."""
    await check_rate_limit("portal_auth_login", request, limit=10, window_seconds=60)
    # Per-ACCOUNT brake, mirroring the employee twin — the per-IP cap above is
    # blind to a spray distributed across rotating addresses. Keyed on the
    # SUBMITTED address BEFORE the lookup so an unknown one throttles
    # identically (the 429 must not become the enumeration oracle the
    # equalised-timing 401 below exists to avoid), and scoped by tenant slug
    # because a vendor address is only unique WITHIN a tenant DB — keying on
    # the address alone would let one tenant's traffic throttle another
    # tenant's supplier.
    identity = auth_identity_key(slug, body.email)
    await check_auth_failures(
        "portal_login",
        identity,
        limit=LOGIN_FAILURE_LIMIT,
        window_seconds=LOGIN_FAILURE_WINDOW_SECONDS,
    )
    ip = resolve_client_ip(request) or "unknown"
    result = await db.execute(select(VendorUser).where(VendorUser.email == body.email))
    vu = result.scalar_one_or_none()

    if not vu or not vu.hashed_password or not vu.is_active:
        # Equalize timing with the wrong-password path so the response time
        # doesn't reveal whether a vendor account exists (enumeration).
        await dummy_verify()
        await record_auth_failure(
            "portal_login", identity, window_seconds=LOGIN_FAILURE_WINDOW_SECONDS
        )
        # A rejection against an account we CAN identify is auditable; a truly
        # unknown address has no org and so no tenant trail to write into. Same
        # split the employee twin makes. Someone hammering a deactivated
        # supplier login is exactly what an admin wants to see.
        if vu is not None:
            await _audit_portal_login_failure(
                vu, ip=ip, reason="no_password" if not vu.hashed_password else "inactive"
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not await verify_password(body.password, vu.hashed_password):
        await record_auth_failure(
            "portal_login", identity, window_seconds=LOGIN_FAILURE_WINDOW_SECONDS
        )
        # Until now a brute-force against a supplier account left no trace at
        # all — the employee twin has written `auth.login.failure` since it was
        # built. PII-lean: the vendor user is identified by id, so the trail
        # doesn't restate the supplier contact's address.
        await _audit_portal_login_failure(vu, ip=ip, reason="bad_password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    await clear_auth_failures("portal_login", identity)

    vu.last_login_at = datetime.now(UTC)
    await db.commit()

    # MFA gate. The master switch (`FEOH_MFA_ENABLED`) wins — if MFA is off at the
    # platform level we skip even an enrolled vendor, keeping local-dev login
    # painless. MFA is opt-in per vendor user (no org-wide enforcement yet).
    if settings.mfa_enabled and vu.mfa_enabled and vu.mfa_secret:
        challenge_token = mfa.create_vendor_challenge_token(vu.id)
        return PortalMFAChallengeResponse(mfa_challenge_token=challenge_token)

    return await _mint_portal_session(vu, request, method="password")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def portal_logout(authorization: str = Header()):
    token = authorization.removeprefix("Bearer ")
    payload = decode_token(token)
    # Only a vendor-portal token may be revoked here. Without this guard the
    # endpoint accepted ANY JWT signed with FEOH_SECRET_KEY — including an employee
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
        try:
            vu_id = uuid.UUID(payload["sub"])
        except (KeyError, ValueError, TypeError):
            # Malformed subject — still revoke the token, just nothing to untrack.
            await block_token(jti, ttl)
        else:
            # Blocklist AND drop it from the tracking set, so a signed-out
            # session can't linger in the supplier's session list.
            await end_session(vu_id, jti, ttl)


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
    authorization: str | None = Header(default=None),
):
    """Change the authenticated supplier user's password.

    Signs the supplier out of every OTHER device on success — the same
    guarantee the employee surface and the admin-initiated reset give. A
    supplier changing their password is usually doing it because they think the
    old one leaked; leaving the other sessions authenticating with a token
    minted under that password for the rest of its lifetime defeats the change.
    The session making the request is spared.
    """
    if not vu.hashed_password or not await verify_password(
        body.current_password, vu.hashed_password
    ):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    try:
        validate_password_complexity(body.new_password)
    except PasswordError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    vu.hashed_password = await hash_password(body.new_password)
    vu.must_change_password = False
    await db.commit()

    # AFTER the commit, so a Redis hiccup can't roll back a password the caller
    # was told was changed. `get_current_vendor_user` doesn't stash the JTI (it
    # lives in the portal dependency tree, which deliberately shares nothing
    # with the employee one), so the caller's own session is identified by
    # re-decoding the bearer token the request already presented.
    current_jti = None
    if authorization and authorization.startswith("Bearer "):
        current_jti = decode_token(authorization.removeprefix("Bearer ")).get("jti")
    revoked = await revoke_other_sessions(vu.id, current_jti)
    if vu.organization_id:
        await dispatch_auth_audit(
            organization_id=vu.organization_id,
            actor_id=None,
            action="portal.session.revoked",
            entity_id=vu.id,
            details={"scope": "others", "revoked": len(revoked), "reason": "password_changed"},
        )

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

    # Per-ACCOUNT cap on the mail we actually send — the per-IP limit above
    # can't stop one valid challenge token replayed from rotating addresses
    # bombing the supplier's inbox. Mirrors the employee twin.
    await check_rate_limit(
        "portal_auth_mfa_email_account",
        limit=EMAIL_OTP_PER_ACCOUNT_PER_HOUR,
        window_seconds=3600,
        subject=str(vu.id),
    )

    code = await mfa.issue_vendor_email_otp(vu.id)
    await _send_vendor_email_otp(vu, code)


@router.post("/mfa/challenge")
async def portal_mfa_challenge(
    body: PortalMFAChallengeVerifyRequest,
    request: Request,
    slug: str = Depends(get_tenant_slug),
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

    # Per-ACCOUNT brake on the second factor, mirroring the employee twin: an
    # attacker here already holds the supplier's password and can mint fresh
    # challenge tokens, so only a budget keyed on the account puts the 10^6
    # TOTP keyspace out of reach. Tenant-scoped like the login bucket.
    mfa_identity = auth_identity_key(slug, str(vu.id))
    await check_auth_failures(
        "portal_mfa",
        mfa_identity,
        limit=MFA_FAILURE_LIMIT,
        window_seconds=MFA_FAILURE_WINDOW_SECONDS,
    )

    if body.method == "email":
        if not await mfa.verify_vendor_email_otp(vu.id, body.code):
            await record_auth_failure(
                "portal_mfa", mfa_identity, window_seconds=MFA_FAILURE_WINDOW_SECONDS
            )
            raise HTTPException(status_code=401, detail="Invalid or expired code")
    else:
        if not await mfa.verify_totp(vu.mfa_secret, body.code):
            await record_auth_failure(
                "portal_mfa", mfa_identity, window_seconds=MFA_FAILURE_WINDOW_SECONDS
            )
            raise HTTPException(status_code=401, detail="Invalid code")

    await clear_auth_failures("portal_mfa", mfa_identity)

    # Single-use: burn the challenge token now that the factor is verified so
    # it can't be replayed to mint a second session (issue #162).
    await mfa.consume_challenge_token(claims.jti)

    return await _mint_portal_session(vu, request, method="mfa")


# Mirrors `api/auth.STEP_UP_RATE_LIMIT_PER_MINUTE` — a credential-management
# endpoint that checks a password is a password oracle unless it is throttled,
# and a silent one unless it is audited. Keyed on the vendor USER, not the
# client IP: the attacker already holds their token and can rotate IPs.
STEP_UP_RATE_LIMIT_PER_MINUTE = 5


async def _audit_portal_step_up_failure(vu: VendorUser, *, operation: str) -> None:
    """Record a failed re-authentication against a vendor's factor change.

    PII-free — `operation` is a fixed literal, the submitted credential never
    enters the trail. Skipped for a legacy row with no `organization_id` (the
    audit dispatcher resolves the tenant DB from it); `dispatch_auth_audit`
    swallows its own failures, so this never breaks the request either way.
    """
    if not vu.organization_id:
        return
    await dispatch_auth_audit(
        organization_id=vu.organization_id,
        actor_id=vu.id,
        action="portal.mfa.step_up.failure",
        entity_id=vu.id,
        details={"operation": operation},
    )


async def _require_portal_mfa_step_up(
    vu: VendorUser, body: PortalMFAStepUpRequest | None, *, operation: str
) -> None:
    """Gate any change to a vendor user's *existing* second factor.

    Same rule as the employee surface (`api/auth._require_mfa_step_up`): a
    first enrollment is frictionless, but once a factor is live, replacing it
    needs the portal password or a code from the current authenticator. A
    stolen portal session must not be able to strip or hijack MFA. Throttled
    per-account and audited on failure, like the employee twin.
    """
    if not (vu.mfa_enabled and vu.mfa_secret):
        return
    await check_rate_limit(
        "portal_auth_mfa_step_up",
        limit=STEP_UP_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
        subject=str(vu.id),
    )
    if await mfa.step_up_verified(
        hashed_password=vu.hashed_password,
        mfa_secret=vu.mfa_secret,
        password=body.password if body else None,
        code=body.code if body else None,
    ):
        return
    await _audit_portal_step_up_failure(vu, operation=operation)
    raise HTTPException(
        status_code=400,
        detail=(
            "Confirm your password or a current authenticator code to change "
            "your two-factor settings."
        ),
    )


@router.post("/mfa/enroll", response_model=PortalMFAEnrollStartResponse)
async def portal_mfa_enroll(
    body: PortalMFAStepUpRequest | None = None,
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Start TOTP enrollment — mint a candidate secret + QR.

    The candidate waits in Redis (`services/mfa`), NOT on the vendor-user row:
    a factor already in force survives the whole ceremony and is only replaced
    once `/mfa/verify` proves the vendor holds the new one. Re-enrolling over a
    live factor also requires a step-up.
    """
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")

    await _require_portal_mfa_step_up(vu, body, operation="totp_enroll")

    secret = mfa.generate_totp_secret()
    await mfa.stash_pending_vendor_totp_secret(vu.id, secret)

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
    """Confirm the vendor can produce a code for the *pending* secret, then
    make it the account's factor.

    The only place a TOTP secret is written to `vendor_users`. Until it
    succeeds the previous factor stays live, so an abandoned enrollment can't
    leave the portal account single-factor.
    """
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")
    pending = await mfa.read_pending_vendor_totp_secret(vu.id)
    if not pending:
        raise HTTPException(status_code=400, detail="Start enrollment first")
    if not await mfa.verify_totp(pending, body.code):
        raise HTTPException(status_code=401, detail="Invalid code")

    vu.mfa_secret = pending
    vu.mfa_enabled = True
    vu.mfa_enrolled_at = datetime.now(UTC)
    await db.commit()
    await mfa.clear_pending_vendor_totp_secret(vu.id)

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
    a stolen session shouldn't be able to silently strip MFA off. Throttled +
    audited on failure like the enroll step-up."""
    if not vu.mfa_enabled or not vu.mfa_secret:
        raise HTTPException(status_code=400, detail="MFA is not enabled")
    await check_rate_limit(
        "portal_auth_mfa_step_up",
        limit=STEP_UP_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
        subject=str(vu.id),
    )
    if not await mfa.verify_totp(vu.mfa_secret, body.code):
        await _audit_portal_step_up_failure(vu, operation="totp_disable")
        raise HTTPException(status_code=401, detail="Invalid code")

    vu.mfa_secret = None
    vu.mfa_enabled = False
    vu.mfa_enrolled_at = None
    await db.commit()
    # Drop any half-finished enrollment so a candidate minted before the
    # disable can't be promoted by a later verify call.
    await mfa.clear_pending_vendor_totp_secret(vu.id)

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
