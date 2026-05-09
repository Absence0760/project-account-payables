"""Self-service tenant signup.

Two-step flow:

  1. POST /api/signup/start
     Validates input (including captcha + rate limit), creates an
     EmailVerification row, and emails a verification link. No tenant
     resources are created at this stage — the DB, org, and admin user
     don't exist yet.

  2. POST /api/signup/complete
     Consumes the verification token, validates the slug is still
     available, provisions the tenant (DB + org + admin user with a
     generated temp password), and emails the welcome message with the
     tenant URL + credentials.

The split protects against:

  - Fake / stolen email submissions: nothing is created until the user
    proves they received the email and clicked the link.
  - Abusive slug squatting: a slug is only locked up once the verification
    is consumed, so abandoned signups don't waste namespace.
  - Partial failures: if provisioning fails mid-way at /complete, the
    verification token is left unconsumed and can be retried.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_control_db
from app.models.signup import EmailVerification
from app.schemas.signup import (
    SignupCompleteRequest,
    SignupCompleteResponse,
    SignupStartRequest,
    SignupStartResponse,
    SlugCheckResponse,
)
from app.services.email_adapters import EmailMessage, get_email_adapter
from app.services.rate_limit import check_rate_limit
from app.services.tenant_provisioning import provision_tenant
from app.utils.hcaptcha import CaptchaError, verify_captcha
from app.utils.passwords import generate_temp_password
from app.utils.slug import SlugError, ensure_slug_available, validate_slug_format

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/signup", tags=["signup"])

# Very permissive shape check — the real validation is the verification email
# round-trip. Rejects obvious garbage (no @, trailing dots) and keeps us from
# adding the email-validator dep.
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

VERIFICATION_TTL = timedelta(hours=24)


def _tenant_url(slug: str) -> str:
    template = settings.tenant_url_template or "http://{slug}.localhost:7777"
    return template.replace("{slug}", slug)


def _public_url(path: str) -> str:
    base = (settings.public_url or "http://localhost:7777").rstrip("/")
    return f"{base}{path}"


def _validate_email_shape(email: str) -> None:
    if not _EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")


@router.get("/slug-check", response_model=SlugCheckResponse)
async def slug_check(slug: str, db: AsyncSession = Depends(get_control_db)):
    """Cheap availability check for the signup form's inline validation."""
    try:
        await ensure_slug_available(slug, db)
        return SlugCheckResponse(slug=slug, available=True)
    except SlugError as exc:
        return SlugCheckResponse(slug=slug, available=False, reason=str(exc))


@router.post("/start", response_model=SignupStartResponse)
async def signup_start(
    body: SignupStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_control_db),
):
    # 1. Rate limit BEFORE anything expensive.
    await check_rate_limit(
        "signup_start",
        request,
        limit=settings.signup_rate_limit_per_hour,
        window_seconds=3600,
    )

    # 2. Input validation.
    _validate_email_shape(body.admin_email)
    try:
        validate_slug_format(body.slug)
    except SlugError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 3. Captcha.
    client_ip = request.client.host if request.client else None
    try:
        await verify_captcha(body.captcha_token, client_ip)
    except CaptchaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 4. Availability check at start-time (best-effort; re-checked at complete).
    try:
        await ensure_slug_available(body.slug, db)
    except SlugError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # 5. Create the verification token.
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + VERIFICATION_TTL
    verification = EmailVerification(
        token=token,
        email=body.admin_email,
        company_name=body.company_name,
        slug=body.slug,
        admin_name=body.admin_name,
        expires_at=expires_at,
        meta={"ip": client_ip or "", "user_agent": request.headers.get("user-agent", "")},
    )
    db.add(verification)
    await db.flush()

    # 6. Send the verification email.
    verify_link = _public_url(f"/verify?token={token}")
    email = get_email_adapter()
    try:
        await email.send(
            EmailMessage(
                to=body.admin_email,
                subject="Verify your Account Payables workspace",
                body_text=(
                    f"Hi {body.admin_name},\n\n"
                    f"Someone (hopefully you) requested to create the '{body.slug}' "
                    f"workspace on Account Payables. Click the link below to "
                    f"confirm and finish setting up your tenant:\n\n"
                    f"{verify_link}\n\n"
                    f"This link expires in 24 hours. If you didn't request this, "
                    f"you can safely ignore this email.\n"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send verification email to %s", body.admin_email)
        raise HTTPException(
            status_code=502,
            detail="Couldn't send the verification email. Please try again.",
        ) from exc

    return SignupStartResponse(
        message=(
            f"We sent a verification link to {body.admin_email}. "
            "Click it within 24 hours to finish creating your workspace."
        )
    )


@router.post("/complete", response_model=SignupCompleteResponse)
async def signup_complete(
    body: SignupCompleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_control_db),
):
    await check_rate_limit(
        "signup_complete",
        request,
        limit=settings.signup_rate_limit_per_hour * 2,
        window_seconds=3600,
    )

    # 1. Look up the verification.
    result = await db.execute(
        select(EmailVerification).where(EmailVerification.token == body.token)
    )
    verification = result.scalar_one_or_none()
    if verification is None:
        raise HTTPException(status_code=404, detail="Verification link is invalid.")

    if verification.consumed_at is not None:
        raise HTTPException(status_code=410, detail="This link has already been used.")

    if verification.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="This link has expired.")

    # 2. Re-check slug availability in case another signup raced ahead.
    try:
        await ensure_slug_available(verification.slug, db)
    except SlugError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # 3. Provision the tenant.
    temp_password = generate_temp_password()
    try:
        await provision_tenant(
            company_name=verification.company_name,
            slug=verification.slug,
            admin_email=verification.email,
            admin_name=verification.admin_name,
            admin_password=temp_password,
            plan="free",
            must_change_password=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Provisioning failed for slug=%s", verification.slug)
        raise HTTPException(
            status_code=500,
            detail=("Something went wrong while creating your workspace. Please contact support."),
        ) from exc

    # 4. Mark verification consumed (same txn as the commits already made by
    #    provision_tenant's own session — this row lives in a different
    #    session but is tracked on db here).
    verification.consumed_at = datetime.now(UTC)
    await db.flush()

    # 5. Send the welcome email with the tenant URL and credentials.
    tenant_url = _tenant_url(verification.slug)
    email_adapter = get_email_adapter()
    try:
        await email_adapter.send(
            EmailMessage(
                to=verification.email,
                subject=f"Your Account Payables workspace '{verification.slug}' is ready",
                body_text=(
                    f"Hi {verification.admin_name},\n\n"
                    f"Your workspace is live.\n\n"
                    f"  URL:      {tenant_url}\n"
                    f"  Email:    {verification.email}\n"
                    f"  Password: {temp_password}\n\n"
                    f"You'll be asked to change your password when you first sign in.\n\n"
                    f"Welcome aboard!\n"
                ),
            )
        )
    except Exception:  # noqa: BLE001
        # Provisioning succeeded — we still return success to the caller, but
        # log loudly so ops can resend manually if needed.
        logger.exception(
            "Welcome email failed for slug=%s. Tenant provisioned; manual resend required.",
            verification.slug,
        )

    return SignupCompleteResponse(
        slug=verification.slug,
        tenant_url=tenant_url,
        admin_email=verification.email,
    )
