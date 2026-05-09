"""Auth endpoints — login (with optional MFA challenge), MFA enroll/verify, logout, profile."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    create_access_token_with_jti,
    decode_token,
    get_current_user,
)
from app.config import settings
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MFAChallengeResponse,
    MFADisableRequest,
    MFAEmailChallengeRequest,
    MFAEnrollStartResponse,
    MFAEnrollVerifyRequest,
    MFAVerifyRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
)
from app.services import mfa
from app.services.audit_dispatch import dispatch_auth_audit
from app.services.email_adapters import EmailMessage, get_email_adapter
from app.services.session_management import end_session, register_session
from app.utils.passwords import PasswordError, validate_password_complexity

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_user_org(db: AsyncSession, org_id) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    return result.scalar_one_or_none()


def _user_response(user: User, org: Organization | None = None) -> UserResponse:
    org_required = mfa.org_requires_mfa(org.settings if org else None)
    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        organization_id=str(user.organization_id),
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        mfa_enabled=user.mfa_enabled,
        mfa_required_by_org=org_required,
        roles=[r.name for r in user.roles],
    )


async def _send_email_otp(user: User, code: str) -> None:
    """Best-effort delivery — log + raise back if the adapter blows up."""
    msg = EmailMessage(
        to=user.email,
        subject="Your sign-in code",
        body_text=(
            f"Hi {user.full_name},\n\n"
            f"Your sign-in code is: {code}\n\n"
            f"It expires in {settings.mfa_email_otp_ttl_seconds // 60} minutes. "
            "If you didn't try to sign in, ignore this email and consider "
            "rotating your password."
        ),
    )
    adapter = get_email_adapter()
    await adapter.send(msg)


def _client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


# ---------------------------------------------------------------------------
# Login + MFA flow
# ---------------------------------------------------------------------------


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_control_db),
):
    """Password login. Returns either a final access token or — if MFA is in
    play — a short-lived challenge token the browser trades for one."""
    ip = _client_ip(request)
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password:
        # Failed login for an unknown user — still audit-log so abuse is
        # visible. Without an organization_id we can't pick a tenant DB,
        # so the write is simply dropped (logged at WARN inside the helper).
        if user is not None:
            await dispatch_auth_audit(
                organization_id=user.organization_id,
                actor_id=None,
                action="auth.login.failure",
                details={"email": body.email, "ip": ip, "reason": "no_password"},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not pwd_context.verify(body.password, user.hashed_password):
        await dispatch_auth_audit(
            organization_id=user.organization_id,
            actor_id=None,
            action="auth.login.failure",
            details={"email": body.email, "ip": ip, "reason": "bad_password"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    org = await _load_user_org(db, user.organization_id)
    org_required = mfa.org_requires_mfa(org.settings if org else None)

    # MFA gate. The master switch (`AP_MFA_ENABLED`) wins — if MFA is off
    # at the platform level, we skip even when an individual user is enrolled.
    # This keeps local-dev login painless.
    if settings.mfa_enabled and (user.mfa_enabled or org_required):
        challenge_token = mfa.create_challenge_token(user.id)
        # If the org enforces MFA but the user hasn't enrolled, only `email`
        # is offered as a method — and the verify path returns an "enroll
        # required" response. The browser routes them to the enrollment page.
        methods = []
        if user.mfa_enabled:
            methods.append("totp")
        # Email backup is always available as long as the account has an email
        # (which they all do — it's the login identifier). For an unenrolled
        # user under org-enforcement we still offer email so they can prove
        # ownership of the inbox before we let them enroll.
        methods.append("email")
        await dispatch_auth_audit(
            organization_id=user.organization_id,
            actor_id=user.id,
            action="auth.mfa.challenge_issued",
            entity_id=user.id,
            details={
                "methods": methods,
                "ip": ip,
                "must_enroll": org_required and not user.mfa_enabled,
            },
        )
        return MFAChallengeResponse(
            mfa_challenge_token=challenge_token,
            methods=methods,
            must_enroll=org_required and not user.mfa_enabled,
        )

    token, jti = create_access_token_with_jti(user.id, user.organization_id)
    await register_session(user.id, jti)
    await dispatch_auth_audit(
        organization_id=user.organization_id,
        actor_id=user.id,
        action="auth.login.success",
        entity_id=user.id,
        details={"ip": ip, "method": "password"},
    )
    return TokenResponse(
        access_token=token,
        must_change_password=user.must_change_password,
    )


@router.post("/mfa/challenge/email", status_code=status.HTTP_204_NO_CONTENT)
async def request_email_otp(
    body: MFAEmailChallengeRequest,
    db: AsyncSession = Depends(get_control_db),
):
    """Generate + email a one-time code. The challenge_token from /login proves
    the password was already accepted, so we don't email codes to random people."""
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled")
    try:
        user_id = mfa.decode_challenge_token(body.challenge_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        # Don't leak which UUIDs exist — return 204 anyway.
        return

    code = await mfa.issue_email_otp(user.id)
    await _send_email_otp(user, code)


@router.post("/mfa/verify", response_model=TokenResponse)
async def verify_mfa(
    body: MFAVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_control_db),
):
    """Trade a challenge token + valid code for a real access token."""
    ip = _client_ip(request)
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled")
    try:
        user_id = mfa.decode_challenge_token(body.challenge_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid challenge")

    if body.method == "totp":
        if not user.mfa_enabled or not user.mfa_secret:
            raise HTTPException(status_code=400, detail="TOTP not enrolled for this account")
        if not mfa.verify_totp(user.mfa_secret, body.code):
            await dispatch_auth_audit(
                organization_id=user.organization_id,
                actor_id=user.id,
                action="auth.mfa.verify.failure",
                entity_id=user.id,
                details={"method": "totp", "ip": ip},
            )
            raise HTTPException(status_code=401, detail="Invalid code")
    elif body.method == "email":
        if not await mfa.verify_email_otp(user.id, body.code):
            await dispatch_auth_audit(
                organization_id=user.organization_id,
                actor_id=user.id,
                action="auth.mfa.verify.failure",
                entity_id=user.id,
                details={"method": "email", "ip": ip},
            )
            raise HTTPException(status_code=401, detail="Invalid or expired code")

    token, jti = create_access_token_with_jti(user.id, user.organization_id)
    await register_session(user.id, jti)
    await dispatch_auth_audit(
        organization_id=user.organization_id,
        actor_id=user.id,
        action="auth.mfa.verify.success",
        entity_id=user.id,
        details={"method": body.method, "ip": ip},
    )
    await dispatch_auth_audit(
        organization_id=user.organization_id,
        actor_id=user.id,
        action="auth.login.success",
        entity_id=user.id,
        details={"ip": ip, "method": f"password+mfa:{body.method}"},
    )
    return TokenResponse(
        access_token=token,
        must_change_password=user.must_change_password,
    )


# ---------------------------------------------------------------------------
# Per-user MFA enrollment management
# ---------------------------------------------------------------------------


@router.post("/mfa/enroll", response_model=MFAEnrollStartResponse)
async def enroll_mfa_start(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Start TOTP enrollment — mint (or re-issue) the secret + QR. The secret
    is held in pending state until /mfa/enroll/verify completes."""
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")

    secret = mfa.generate_totp_secret()
    user.mfa_secret = secret
    user.mfa_enabled = False  # not active until verified
    user.mfa_enrolled_at = None
    await db.commit()

    uri = mfa.provisioning_uri(secret, account_label=user.email)
    return MFAEnrollStartResponse(
        secret=secret,
        provisioning_uri=uri,
        qr_code_data_url=mfa.qr_code_data_url(uri),
    )


@router.post("/mfa/enroll/verify", response_model=UserResponse)
async def enroll_mfa_verify(
    body: MFAEnrollVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Confirm the user can produce a valid code, then flip MFA on."""
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")
    if not user.mfa_secret:
        raise HTTPException(status_code=400, detail="Start enrollment first")
    if not mfa.verify_totp(user.mfa_secret, body.code):
        raise HTTPException(status_code=401, detail="Invalid code")

    user.mfa_enabled = True
    user.mfa_enrolled_at = datetime.now(UTC)
    await db.commit()
    org = await _load_user_org(db, user.organization_id)
    return _user_response(user, org)


@router.post("/mfa/disable", response_model=UserResponse)
async def disable_mfa(
    body: MFADisableRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Turn off TOTP for this account. Requires password re-entry — a stolen
    session shouldn't be able to silently strip MFA off."""
    if not user.hashed_password or not pwd_context.verify(body.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Password is incorrect")

    org = await _load_user_org(db, user.organization_id)
    if mfa.org_requires_mfa(org.settings if org else None):
        raise HTTPException(
            status_code=400,
            detail="Your organization requires MFA — disabling is not allowed.",
        )

    user.mfa_secret = None
    user.mfa_enabled = False
    user.mfa_enrolled_at = None
    await db.commit()
    return _user_response(user, org)


# ---------------------------------------------------------------------------
# Existing endpoints — logout, profile, change-password
# ---------------------------------------------------------------------------


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, authorization: str = Header()):
    """Revoke the current token by adding it to the Redis blocklist and
    untracking it from the user's active-session set."""
    token = authorization.removeprefix("Bearer ")
    payload = decode_token(token)
    jti = payload.get("jti")
    sub = payload.get("sub")
    org_raw = payload.get("org")
    if jti and sub:
        exp = payload.get("exp", 0)
        import time
        import uuid as _uuid

        ttl = max(int(exp - time.time()), 1)
        try:
            user_id = _uuid.UUID(sub)
            await end_session(user_id, jti, ttl)
        except (ValueError, TypeError):
            # Fallback: at minimum blocklist the token even if the sub is malformed
            from app.redis import block_token as _block_token

            await _block_token(jti, ttl)
            user_id = None

        if user_id and org_raw:
            try:
                await dispatch_auth_audit(
                    organization_id=_uuid.UUID(org_raw),
                    actor_id=user_id,
                    action="auth.logout",
                    entity_id=user_id,
                    details={"ip": _client_ip(request)},
                )
            except (ValueError, TypeError):
                pass


@router.get("/me", response_model=UserResponse)
async def get_me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    org = await _load_user_org(db, user.organization_id)
    return _user_response(user, org)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    body: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    if body.full_name is not None:
        user.full_name = body.full_name

    if body.password is not None:
        if not body.current_password:
            raise HTTPException(
                status_code=400, detail="Current password is required to set a new password"
            )
        if not user.hashed_password or not pwd_context.verify(
            body.current_password, user.hashed_password
        ):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        user.hashed_password = pwd_context.hash(body.password)

    await db.commit()
    org = await _load_user_org(db, user.organization_id)
    return _user_response(user, org)


@router.post("/change-password", response_model=UserResponse)
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Change password for the authenticated user.

    Used by the first-login forced-change flow (`must_change_password=true`)
    and by any user voluntarily rotating their password. Clearing the flag
    happens regardless — successfully setting a password means the temp
    credential is no longer in play.
    """
    if not user.hashed_password or not pwd_context.verify(
        body.current_password, user.hashed_password
    ):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    try:
        validate_password_complexity(body.new_password)
    except PasswordError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user.hashed_password = pwd_context.hash(body.new_password)
    user.must_change_password = False
    await db.commit()
    org = await _load_user_org(db, user.organization_id)
    return _user_response(user, org)


# ---------- Delegation / Out-of-Office ----------


class SetDelegateRequest(BaseModel):
    delegate_to_id: str
    until: str  # ISO datetime


class DelegationResponse(BaseModel):
    delegate_to_id: str | None = None
    delegate_to_name: str | None = None
    until: str | None = None
    is_active: bool = False


@router.get("/delegation", response_model=DelegationResponse)
async def get_delegation(
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(get_current_user),
):
    """Get current user's delegation status."""
    from datetime import UTC, datetime

    is_active = bool(
        user.delegate_to_id and user.delegate_until and user.delegate_until > datetime.now(UTC)
    )
    delegate_name = None
    if user.delegate_to_id:
        result = await db.execute(select(User).where(User.id == user.delegate_to_id))
        delegate = result.scalar_one_or_none()
        if delegate:
            delegate_name = delegate.full_name

    return DelegationResponse(
        delegate_to_id=str(user.delegate_to_id) if user.delegate_to_id else None,
        delegate_to_name=delegate_name,
        until=user.delegate_until.isoformat() if user.delegate_until else None,
        is_active=is_active,
    )


@router.post("/delegation", response_model=DelegationResponse)
async def set_delegation(
    body: SetDelegateRequest,
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(get_current_user),
):
    """Set out-of-office delegation — approvals route to delegate."""
    import uuid as _uuid
    from datetime import datetime

    delegate_id = _uuid.UUID(body.delegate_to_id)
    if delegate_id == user.id:
        raise HTTPException(status_code=422, detail="Cannot delegate to yourself.")

    # Verify delegate exists and is in same org
    result = await db.execute(select(User).where(User.id == delegate_id))
    delegate = result.scalar_one_or_none()
    if not delegate or delegate.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Delegate user not found.")

    user.delegate_to_id = delegate_id
    user.delegate_until = datetime.fromisoformat(body.until)
    await db.commit()

    return DelegationResponse(
        delegate_to_id=str(delegate_id),
        delegate_to_name=delegate.full_name,
        until=user.delegate_until.isoformat(),
        is_active=True,
    )


@router.delete("/delegation", status_code=204)
async def clear_delegation(
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(get_current_user),
):
    """Clear out-of-office delegation."""
    user.delegate_to_id = None
    user.delegate_until = None
    await db.commit()
