"""Auth endpoints — login (with optional MFA challenge), MFA enroll/verify, logout, profile."""

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    create_access_token_with_jti,
    decode_token,
    get_current_user,
)
from app.api.permissions import effective_permissions
from app.config import settings
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.models.webauthn_credential import WebAuthnCredential
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MFAChallengeResponse,
    MFADisableRequest,
    MFAEmailChallengeRequest,
    MFAEnrollStartResponse,
    MFAEnrollVerifyRequest,
    MFAStepUpRequest,
    MFAVerifyRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
    WebAuthnAuthFinishRequest,
    WebAuthnAuthStartRequest,
    WebAuthnAuthStartResponse,
    WebAuthnCredentialResponse,
    WebAuthnRegisterFinishRequest,
    WebAuthnRegisterStartResponse,
    WebAuthnStepUpStartRequest,
)
from app.services import mfa, webauthn
from app.services.audit_dispatch import dispatch_auth_audit
from app.services.email_adapters import (
    EmailMessage,
    get_email_adapter,
    is_supported_locale,
)
from app.services.rate_limit import check_rate_limit
from app.services.session_management import end_session, register_session
from app.services.sso import is_sso_only
from app.utils.passwords import (
    PasswordError,
    dummy_verify,
    pwd_context,
    validate_password_complexity,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_user_org(db: AsyncSession, org_id) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    return result.scalar_one_or_none()


async def _user_passkeys(db: AsyncSession, user_id) -> list[WebAuthnCredential]:
    """All registered passkeys for a user (control-plane). Empty list = none."""
    result = await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)
    )
    return list(result.scalars().all())


async def _verify_presented_assertion(
    db: AsyncSession,
    user_id,
    credential_json: str,
    *,
    purpose: str,
    operation: str | None = None,
) -> WebAuthnCredential:
    """Resolve the presented passkey and verify its assertion.

    The single place a ``navigator.credentials.get()`` response is checked —
    shared by passkey LOGIN and passkey STEP-UP so the two can't drift on
    credential-ownership scoping, counter bumping or clone detection. The
    lookup is scoped to ``user_id``, so a caller can never present somebody
    else's passkey.

    Raises ``webauthn.WebAuthnError`` on every failure — unknown credential id,
    bad signature, wrong / expired / already-consumed challenge, counter
    regression — and the caller turns that into its own opaque response, so the
    reason never leaks. On success the credential's signature counter and
    ``last_used_at`` are updated on the session; the caller persists them.
    """
    presented_id = webauthn.extract_credential_id(credential_json)
    cred = None
    if presented_id:
        result = await db.execute(
            select(WebAuthnCredential).where(
                WebAuthnCredential.user_id == user_id,
                WebAuthnCredential.credential_id == presented_id,
            )
        )
        cred = result.scalar_one_or_none()
    if not cred:
        raise webauthn.WebAuthnError("Unknown credential")

    cred.sign_count = await webauthn.finish_authentication(
        user_id=user_id,
        credential_json=credential_json,
        stored_public_key=cred.public_key,
        stored_sign_count=cred.sign_count,
        purpose=purpose,
        operation=operation,
    )
    cred.last_used_at = datetime.now(UTC)
    return cred


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
        # Effective granular permissions for the SPA's `can(perm)` gate. Resolved
        # off the user's roles (system via the default map, custom via their
        # stored list) — the same union `require_permission` enforces server-side,
        # so the UI gate and the backend gate can't drift.
        permissions=sorted(effective_permissions(user.roles)),
        locale=user.locale,
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
    # Per-IP credential-stuffing brake. 10 attempts / minute is high enough
    # for a fat-fingering human (refreshing the page, retrying after a typo)
    # and low enough to make online brute-forcing untenable.
    await check_rate_limit("auth_login", request, limit=10, window_seconds=60)
    ip = _client_ip(request)
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password:
        # Equalize timing with the wrong-password path below so the response
        # time doesn't reveal whether the email has an account (enumeration).
        dummy_verify()
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

    # SSO-only enforcement. When the tenant requires SSO, password login is
    # closed even for users who still carry a password hash — checked after the
    # password is verified so it reuses the org load and doesn't perturb the
    # unknown-vs-wrong-password enumeration parity. (Passwordless SSO users
    # already 401'd above; the login page hides the password form when
    # sso_only, so they use the IdP button.)
    if is_sso_only(org.settings if org else None):
        await dispatch_auth_audit(
            organization_id=user.organization_id,
            actor_id=None,
            action="auth.login.failure",
            details={"email": body.email, "ip": ip, "reason": "sso_only"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This workspace requires single sign-on. Sign in with your identity provider.",
        )

    org_required = mfa.org_requires_mfa(org.settings if org else None)

    # MFA gate. The master switch (`FEOH_MFA_ENABLED`) wins — if MFA is off
    # at the platform level, we skip even when an individual user is enrolled.
    # This keeps local-dev login painless. A user who enrolled ONLY a passkey
    # (no TOTP) still has a second factor, so a registered credential also
    # trips the gate.
    passkeys = await _user_passkeys(db, user.id) if settings.mfa_enabled else []
    if settings.mfa_enabled and (user.mfa_enabled or org_required or passkeys):
        challenge_token = mfa.create_challenge_token(user.id)
        # If the org enforces MFA but the user hasn't enrolled, only `email`
        # is offered as a method — and the verify path returns an "enroll
        # required" response. The browser routes them to the enrollment page.
        methods = []
        if user.mfa_enabled:
            methods.append("totp")
        # Passkeys (WebAuthn) are a separate factor — offered whenever the user
        # has at least one registered credential, independent of TOTP. This is
        # the additive passkey login path; TOTP/email remain unchanged.
        if passkeys:
            methods.append("passkey")
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
    request: Request,
    db: AsyncSession = Depends(get_control_db),
):
    """Generate + email a one-time code. The challenge_token from /login proves
    the password was already accepted, so we don't email codes to random people."""
    # Cap how fast a single IP can churn email-OTPs — protects the user's
    # inbox from being weaponised as a notification spammer.
    await check_rate_limit("auth_mfa_email", request, limit=5, window_seconds=60)
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled")
    try:
        claims = await mfa.decode_challenge_token(body.challenge_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = (await db.execute(select(User).where(User.id == claims.subject_id))).scalar_one_or_none()
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
    # TOTP is a 6-digit code (10^6 keyspace). 10 attempts / minute caps an
    # online brute-force at roughly 600/hour, which a 5-minute challenge TTL
    # comfortably outruns. Email-OTP is single-use so the limit there only
    # mitigates timing-attack probing.
    await check_rate_limit("auth_mfa_verify", request, limit=10, window_seconds=60)
    ip = _client_ip(request)
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled")
    try:
        claims = await mfa.decode_challenge_token(body.challenge_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = (await db.execute(select(User).where(User.id == claims.subject_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid challenge")

    if body.method == "totp":
        if not user.mfa_enabled or not user.mfa_secret:
            raise HTTPException(status_code=400, detail="TOTP not enrolled for this account")
        if not await mfa.verify_totp(user.mfa_secret, body.code):
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

    # Single-use: burn the challenge token now that the factor is verified so
    # it can't be replayed to mint a second session from one password check
    # (issue #162).
    await mfa.consume_challenge_token(claims.jti)

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


# A credential-management endpoint that checks a password is a password oracle
# unless it is throttled, and a silent one unless it is audited. These two
# helpers are shared by every MFA-mutating route in this file (`/mfa/enroll`,
# `/mfa/passkey/register`, `/mfa/passkey/{id}` DELETE, `/mfa/disable`) so no
# future one can forget either half. Keyed on the *account*, not the client IP:
# the attacker here already holds the victim's token and can rotate IPs freely,
# so per-IP throttling would miss them entirely.
STEP_UP_RATE_LIMIT_PER_MINUTE = 5


async def _throttle_step_up(user_id) -> None:
    await check_rate_limit(
        "auth_mfa_step_up",
        limit=STEP_UP_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
        subject=str(user_id),
    )


async def _audit_step_up_failure(user: User, *, operation: str) -> None:
    """Record a failed re-authentication against a second-factor change.

    PII-free by construction — `operation` is one of a fixed set of literals;
    the submitted password / code never enters the trail.
    """
    await dispatch_auth_audit(
        organization_id=user.organization_id,
        actor_id=user.id,
        action="auth.mfa.step_up.failure",
        entity_id=user.id,
        details={"operation": operation},
    )


STEP_UP_FAILURE_DETAIL = (
    "Confirm your password, a current authenticator code, or a registered "
    "passkey to change your two-factor settings."
)


async def _step_up_satisfied(
    db: AsyncSession,
    user: User,
    body: MFAStepUpRequest | None,
    *,
    operation: str,
) -> bool:
    """Did the caller re-prove control of the account for `operation`?

    Three proofs, any one of which is enough: the account password, a code from
    the currently enrolled authenticator (both via the pure, shared
    `mfa.step_up_verified`), or a **WebAuthn assertion** from an
    already-registered passkey.

    The assertion path is what makes factor management possible at all for a
    passwordless SSO-only account whose sole factor is a passkey — before it,
    such an account had nothing to challenge and was refused outright. It is
    checked here rather than inside `mfa.step_up_verified` because it needs the
    DB (to resolve the credential) and Redis (the single-use challenge), which
    that pure helper deliberately doesn't touch.

    The assertion is bound to `operation`: it only verifies against the
    challenge `POST /mfa/step-up/passkey` minted for that same operation, so an
    assertion obtained to authorize e.g. `passkey_register` cannot be turned
    around and used to authorize `passkey_delete` — nor can a LOGIN assertion
    satisfy any step-up (different Redis namespace entirely). See
    `services/webauthn._assertion_challenge_key`.
    """
    if await mfa.step_up_verified(
        hashed_password=user.hashed_password,
        mfa_secret=user.mfa_secret,
        password=body.password if body else None,
        code=body.code if body else None,
    ):
        return True
    if body is None or not body.assertion:
        return False
    try:
        await _verify_presented_assertion(
            db,
            user.id,
            json.dumps(body.assertion),
            purpose=webauthn.ASSERTION_PURPOSE_STEP_UP,
            operation=operation,
        )
    except webauthn.WebAuthnError:
        return False
    # Persist the bumped signature counter now. Clone detection is only worth
    # anything if the counter actually moves forward in the database; leaving it
    # to whatever the caller happens to commit later would silently regress it
    # on the routes that write nothing (enroll-start, register-start).
    await db.commit()
    return True


async def _require_mfa_step_up(
    user: User,
    body: MFAStepUpRequest | None,
    *,
    db: AsyncSession,
    operation: str,
    has_passkey: bool = False,
) -> None:
    """Gate any change to an account's *existing* second factor.

    First-time enrollment is deliberately frictionless — an account with no
    factor has nothing to protect and onboarding shouldn't demand a password
    the user just typed. The moment a factor IS in force — a live TOTP secret
    OR at least one registered passkey — adding to it, replacing it, or
    removing it demands one of the three proofs in `_step_up_satisfied`: a
    leaked access token must not be enough to downgrade or hijack the second
    factor.

    An account with no password and no TOTP secret whose only factor is a
    passkey is no longer stuck: it satisfies this with an assertion from that
    passkey. An account with genuinely nothing to challenge still can't, and is
    refused rather than exempted — see `mfa.step_up_verified`.
    """
    has_live_factor = bool(user.mfa_enabled and user.mfa_secret) or has_passkey
    if not has_live_factor:
        return
    await _throttle_step_up(user.id)
    if await _step_up_satisfied(db, user, body, operation=operation):
        return
    await _audit_step_up_failure(user, operation=operation)
    raise HTTPException(status_code=400, detail=STEP_UP_FAILURE_DETAIL)


@router.post("/mfa/enroll", response_model=MFAEnrollStartResponse)
async def enroll_mfa_start(
    body: MFAStepUpRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Start TOTP enrollment — mint a candidate secret + QR.

    The candidate is held in Redis (`services/mfa`), NOT written to the
    account: whatever second factor is already in force stays in force until
    `/mfa/enroll/verify` proves the user holds the new one. Re-enrolling over
    a live factor additionally requires a step-up (password or a code from the
    current authenticator) — see `_require_mfa_step_up`. A registered passkey
    counts as a live factor here too: adding TOTP to a passkey-protected
    account is just as much a factor change as the reverse.
    """
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")

    existing = await _user_passkeys(db, user.id)
    await _require_mfa_step_up(
        user, body, db=db, operation="totp_enroll", has_passkey=bool(existing)
    )

    secret = mfa.generate_totp_secret()
    await mfa.stash_pending_totp_secret(user.id, secret)

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
    """Confirm the user can produce a code for the *pending* secret, then make
    it the account's factor.

    This is the only place a TOTP secret is written to the account row. Until
    it succeeds the previous factor (if any) remains live, so a half-finished
    enrollment can never leave the account with no second factor.
    """
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")
    pending = await mfa.read_pending_totp_secret(user.id)
    if not pending:
        raise HTTPException(status_code=400, detail="Start enrollment first")
    if not await mfa.verify_totp(pending, body.code):
        raise HTTPException(status_code=401, detail="Invalid code")

    user.mfa_secret = pending
    user.mfa_enabled = True
    user.mfa_enrolled_at = datetime.now(UTC)
    await db.commit()
    await mfa.clear_pending_totp_secret(user.id)
    org = await _load_user_org(db, user.organization_id)
    return _user_response(user, org)


@router.post("/mfa/disable", response_model=UserResponse)
async def disable_mfa(
    body: MFADisableRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Turn off TOTP for this account.

    Stripping a factor is the most sensitive factor change there is, so it takes
    the same step-up as every other one in this file — the account password, a
    code from the authenticator being turned off, or an assertion from a
    registered passkey. (An SSO-only account has no password to re-enter; the
    passkey assertion is how it disables its own TOTP.) Throttled + audited on
    failure like the rest.
    """
    await _throttle_step_up(user.id)
    if not await _step_up_satisfied(db, user, body, operation="totp_disable"):
        await _audit_step_up_failure(user, operation="totp_disable")
        raise HTTPException(status_code=400, detail=STEP_UP_FAILURE_DETAIL)

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
    # Drop any half-finished enrollment too, so a candidate minted before the
    # disable can't be promoted afterwards by a later verify call.
    await mfa.clear_pending_totp_secret(user.id)
    return _user_response(user, org)


# ---------------------------------------------------------------------------
# WebAuthn / passkeys — additional MFA factor (separate code path from TOTP)
# ---------------------------------------------------------------------------


def _credential_to_response(c: WebAuthnCredential) -> WebAuthnCredentialResponse:
    return WebAuthnCredentialResponse(
        id=str(c.id),
        name=c.name,
        transports=c.transports,
        created_at=c.created_at.isoformat() if c.created_at else None,
        last_used_at=c.last_used_at.isoformat() if c.last_used_at else None,
    )


@router.post("/mfa/passkey/register", response_model=WebAuthnRegisterStartResponse)
async def passkey_register_start(
    body: MFAStepUpRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Begin passkey enrollment — mint WebAuthn registration options. The
    browser feeds ``options`` to ``navigator.credentials.create()``.

    Adding a factor to an account that already has one is a step-up operation
    for the same reason re-enrolling TOTP is: otherwise a stolen session could
    quietly bind an attacker-controlled authenticator to the account. The
    first factor on a bare account needs no step-up.
    """
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")
    existing = await _user_passkeys(db, user.id)
    await _require_mfa_step_up(
        user, body, db=db, operation="passkey_register", has_passkey=bool(existing)
    )
    options_json = await webauthn.begin_registration(
        user_id=user.id,
        user_name=user.email,
        user_display_name=user.full_name or user.email,
        existing_credential_ids=[c.credential_id for c in existing],
    )
    return WebAuthnRegisterStartResponse(options=json.loads(options_json))


@router.post("/mfa/passkey/register/verify", response_model=WebAuthnCredentialResponse)
async def passkey_register_finish(
    body: WebAuthnRegisterFinishRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Verify the browser's ``create()`` response and persist the passkey."""
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")
    try:
        fields = await webauthn.finish_registration(
            user_id=user.id,
            credential_json=json.dumps(body.credential),
        )
    except webauthn.WebAuthnError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cred = WebAuthnCredential(
        user_id=user.id,
        credential_id=fields["credential_id"],
        public_key=fields["public_key"],
        sign_count=fields["sign_count"],
        transports=fields["transports"],
        name=body.name or "Passkey",
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    await dispatch_auth_audit(
        organization_id=user.organization_id,
        actor_id=user.id,
        action="auth.mfa.passkey.registered",
        entity_id=user.id,
        details={"name": cred.name},
    )
    return _credential_to_response(cred)


@router.get("/mfa/passkey", response_model=list[WebAuthnCredentialResponse])
async def passkey_list(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """List this account's registered passkeys (metadata only)."""
    creds = await _user_passkeys(db, user.id)
    return [_credential_to_response(c) for c in creds]


@router.delete("/mfa/passkey/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def passkey_delete(
    credential_id: str,
    body: MFAStepUpRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Remove one passkey.

    Deleting a factor is a step-up operation for exactly the same reason
    adding one is — a stolen access token must not be able to strip the
    account's second factor. The passkey being deleted IS a live factor, so
    the step-up always applies here; the credentials go in the request BODY
    (never a query string — a password must not land in access logs or
    `Referer` headers).

    On top of that: if the org enforces MFA, the last surviving second factor
    (passkey or TOTP) can't be stripped off at all.
    """
    try:
        cred_uuid = uuid.UUID(credential_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Passkey not found") from exc
    result = await db.execute(
        select(WebAuthnCredential).where(
            WebAuthnCredential.id == cred_uuid,
            WebAuthnCredential.user_id == user.id,
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Passkey not found")

    # Gated after the ownership lookup so an unknown id stays an opaque 404 and
    # doesn't burn the account's step-up throttle. The credential is the
    # caller's own either way — `GET /mfa/passkey` already lists it — so this
    # ordering leaks nothing new.
    await _require_mfa_step_up(user, body, db=db, operation="passkey_delete", has_passkey=True)

    org = await _load_user_org(db, user.organization_id)
    if mfa.org_requires_mfa(org.settings if org else None):
        # Don't let the user delete their last second factor under enforcement.
        remaining = await _user_passkeys(db, user.id)
        other_factor = user.mfa_enabled or len(remaining) > 1
        if not other_factor:
            raise HTTPException(
                status_code=400,
                detail="Your organization requires MFA — keep at least one factor.",
            )

    await db.delete(cred)
    await db.commit()
    await dispatch_auth_audit(
        organization_id=user.organization_id,
        actor_id=user.id,
        action="auth.mfa.passkey.removed",
        entity_id=user.id,
        details={"name": cred.name},
    )


@router.post("/mfa/passkey/authenticate", response_model=WebAuthnAuthStartResponse)
async def passkey_authenticate_start(
    body: WebAuthnAuthStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_control_db),
):
    """Begin a passkey LOGIN challenge. The ``challenge_token`` from /login
    proves the password was already accepted, so we only mint options for that
    user's registered credentials."""
    await check_rate_limit("auth_mfa_passkey", request, limit=10, window_seconds=60)
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled")
    try:
        claims = await mfa.decode_challenge_token(body.challenge_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    creds = await _user_passkeys(db, claims.subject_id)
    if not creds:
        # No passkeys registered — opaque error (don't enumerate factors).
        raise HTTPException(status_code=400, detail="No passkey registered")
    options_json = await webauthn.begin_authentication(
        user_id=claims.subject_id,
        credentials=[{"credential_id": c.credential_id, "transports": c.transports} for c in creds],
        purpose=webauthn.ASSERTION_PURPOSE_LOGIN,
    )
    return WebAuthnAuthStartResponse(options=json.loads(options_json))


@router.post("/mfa/step-up/passkey", response_model=WebAuthnAuthStartResponse)
async def passkey_step_up_start(
    body: WebAuthnStepUpStartRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Begin a passkey STEP-UP challenge for one factor-management operation.

    The counterpart of `/mfa/passkey/authenticate`, but for an *already
    authenticated* caller who now has to re-prove they still hold the
    authenticator before changing the account's factors. The signed assertion
    goes back in the `assertion` field of the mutating call's step-up body
    (`/mfa/enroll`, `/mfa/passkey/register`, `DELETE /mfa/passkey/{id}`,
    `/mfa/disable`) — there is no separate "step-up token" to leak or replay.

    This is the proof a passwordless SSO-only account uses: with no password and
    no TOTP secret, its registered passkey is the only thing it can be
    challenged on.

    The challenge is minted into a Redis slot keyed by (user, step_up,
    `operation`) and consumed single-use by the mutating endpoint, which looks it
    up under its OWN operation — so an assertion collected here for one operation
    can't authorize a different one, and neither can a login assertion authorize
    any of them.
    """
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled on this deployment")
    # Minting a challenge proves nothing and reveals nothing, so this is a
    # cheap-abuse brake rather than the security control (that is
    # `_throttle_step_up` on the verifying call). Keyed on the account for the
    # same reason: the attacker in this threat model already holds the token.
    await check_rate_limit(
        "auth_mfa_step_up_passkey",
        limit=10,
        window_seconds=60,
        subject=str(user.id),
    )
    creds = await _user_passkeys(db, user.id)
    if not creds:
        raise HTTPException(status_code=400, detail="No passkey registered")
    options_json = await webauthn.begin_authentication(
        user_id=user.id,
        credentials=[{"credential_id": c.credential_id, "transports": c.transports} for c in creds],
        purpose=webauthn.ASSERTION_PURPOSE_STEP_UP,
        operation=body.operation,
    )
    return WebAuthnAuthStartResponse(options=json.loads(options_json))


@router.post("/mfa/passkey/authenticate/verify", response_model=TokenResponse)
async def passkey_authenticate_finish(
    body: WebAuthnAuthFinishRequest,
    request: Request,
    db: AsyncSession = Depends(get_control_db),
):
    """Verify a passkey assertion and mint the real access token. The login
    counterpart of /mfa/verify, but for the passkey factor."""
    await check_rate_limit("auth_mfa_passkey", request, limit=10, window_seconds=60)
    ip = _client_ip(request)
    if not settings.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is disabled")
    try:
        claims = await mfa.decode_challenge_token(body.challenge_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = (await db.execute(select(User).where(User.id == claims.subject_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid challenge")

    try:
        # `purpose=login` pins which Redis challenge slot this assertion must
        # match — a step-up assertion signs a challenge from a different slot and
        # therefore cannot mint an access token here.
        await _verify_presented_assertion(
            db,
            claims.subject_id,
            json.dumps(body.credential),
            purpose=webauthn.ASSERTION_PURPOSE_LOGIN,
        )
    except webauthn.WebAuthnError as exc:
        await dispatch_auth_audit(
            organization_id=user.organization_id,
            actor_id=user.id,
            action="auth.mfa.verify.failure",
            entity_id=user.id,
            details={"method": "passkey", "ip": ip},
        )
        raise HTTPException(status_code=401, detail="Invalid passkey") from exc

    await db.commit()

    # Single-use: burn the challenge token now that the passkey factor is
    # verified so it can't be replayed to mint a second session (issue #162).
    await mfa.consume_challenge_token(claims.jti)

    token, jti = create_access_token_with_jti(user.id, user.organization_id)
    await register_session(user.id, jti)
    await dispatch_auth_audit(
        organization_id=user.organization_id,
        actor_id=user.id,
        action="auth.mfa.verify.success",
        entity_id=user.id,
        details={"method": "passkey", "ip": ip},
    )
    await dispatch_auth_audit(
        organization_id=user.organization_id,
        actor_id=user.id,
        action="auth.login.success",
        entity_id=user.id,
        details={"ip": ip, "method": "password+mfa:passkey"},
    )
    return TokenResponse(
        access_token=token,
        must_change_password=user.must_change_password,
    )


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

    # Email-language preference. `locale` in the explicitly-set fields means the
    # caller wants to change it: a supported value sets it, an empty string
    # clears it (→ English fallback), and an UNSUPPORTED value is rejected so the
    # stored preference is always a known locale. Omitting the field leaves it
    # untouched. The user sets their OWN locale (RBAC = the authenticated user).
    if "locale" in body.model_fields_set:
        if not body.locale:
            user.locale = None
        elif is_supported_locale(body.locale):
            user.locale = body.locale
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported locale '{body.locale}'.",
            )

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
