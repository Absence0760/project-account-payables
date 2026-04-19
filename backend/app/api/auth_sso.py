"""OIDC SSO — authorize + callback endpoints.

Flow:
    1. User clicks "Sign in with Okta/Microsoft" on the tenant login page.
    2. Browser hits GET /api/auth/sso/authorize?slug=<tenant>.
       Backend fetches the OIDC discovery doc, mints state + nonce in Redis,
       and 302-redirects the browser to the IdP's authorization endpoint.
    3. User authenticates with the IdP; IdP redirects back to our fixed
       callback path (AP_PUBLIC_URL + AP_SSO_REDIRECT_PATH) with code + state.
    4. Frontend callback page calls POST /api/auth/sso/callback with code+state.
       Backend consumes state (single-use), exchanges code for tokens,
       validates the ID token signature + claims, JIT-provisions the user,
       and returns our own JWT + the tenant slug to redirect to.

The redirect target (frontend /login/sso-callback) is a static path, not
per-tenant — IdPs only need one redirect URI registered per app.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import create_access_token
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import Role, User, UserRole
from app.services.sso import (
    SSOConfigError,
    SSOValidationError,
    build_authorize_url,
    consume_state,
    create_state,
    exchange_code_for_tokens,
    fetch_discovery,
    resolve_sso_config,
    validate_id_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/sso", tags=["auth-sso"])


class SSOConfigPublic(BaseModel):
    """Unauthenticated config surface for the login page. NEVER returns the
    client_secret or SCIM bearer — only whether SSO is available + provider
    label for the button."""

    enabled: bool = False
    provider: str | None = None


class SSOCallbackRequest(BaseModel):
    code: str
    state: str


class SSOCallbackResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False
    tenant_slug: str


# ---------------------------------------------------------------------------


async def _fetch_org_by_slug(slug: str, db: AsyncSession) -> Organization:
    result = await db.execute(select(Organization).where(Organization.slug == slug))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Unknown tenant.")
    return org


@router.get("/config", response_model=SSOConfigPublic)
async def sso_config(slug: str, db: AsyncSession = Depends(get_control_db)):
    """Public endpoint the login page hits to decide whether to render the
    SSO button. Returns only the non-secret bits."""
    org = await _fetch_org_by_slug(slug, db)
    try:
        config = resolve_sso_config(org.settings)
    except SSOConfigError:
        return SSOConfigPublic(enabled=False)
    if config is None:
        return SSOConfigPublic(enabled=False)
    return SSOConfigPublic(enabled=True, provider=config.provider)


@router.get("/authorize")
async def sso_authorize(slug: str, db: AsyncSession = Depends(get_control_db)):
    """302 the browser to the IdP's authorization endpoint."""
    from fastapi.responses import RedirectResponse

    org = await _fetch_org_by_slug(slug, db)
    config = resolve_sso_config(org.settings)
    if config is None:
        raise HTTPException(status_code=400, detail="SSO is not configured for this tenant.")

    discovery = await fetch_discovery(config.discovery_url)
    state, nonce = await create_state(slug)
    url = build_authorize_url(discovery, config.client_id, state, nonce)
    return RedirectResponse(url, status_code=302)


@router.post("/callback", response_model=SSOCallbackResponse)
async def sso_callback(
    body: SSOCallbackRequest,
    db: AsyncSession = Depends(get_control_db),
):
    """Second leg: consume state, exchange code, validate ID token, JIT
    provision the user, mint our own JWT."""
    try:
        bound = await consume_state(body.state)
    except SSOValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tenant_slug = bound["tenant"]
    expected_nonce = bound["nonce"]

    org = await _fetch_org_by_slug(tenant_slug, db)
    config = resolve_sso_config(org.settings)
    if config is None:
        raise HTTPException(status_code=400, detail="SSO is not configured for this tenant.")

    discovery = await fetch_discovery(config.discovery_url)

    try:
        tokens = await exchange_code_for_tokens(
            discovery, config.client_id, config.client_secret, body.code
        )
    except SSOValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="Identity provider did not return an ID token.")

    try:
        claims = await validate_id_token(id_token, discovery, config.client_id, expected_nonce)
    except SSOValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Entra sometimes uses `upn` (user principal name) instead of `email`
    email = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("upn")
    )
    sub = claims.get("sub")
    if not email or not sub:
        raise HTTPException(status_code=400, detail="IdP did not return an email or subject id.")

    email = email.lower().strip()

    # Email-domain allowlist (optional)
    if config.allowed_email_domains:
        domain = email.rsplit("@", 1)[-1]
        if domain not in config.allowed_email_domains:
            raise HTTPException(
                status_code=403,
                detail="Your email domain isn't allowed to sign in to this workspace.",
            )

    user = await _jit_provision(db, org, email, sub, config.provider, claims)

    token = create_access_token(user.id, user.organization_id)
    return SSOCallbackResponse(
        access_token=token,
        must_change_password=user.must_change_password,
        tenant_slug=tenant_slug,
    )


async def _jit_provision(
    db: AsyncSession,
    org: Organization,
    email: str,
    sub: str,
    provider: str,
    claims: dict,
) -> User:
    """Find or create the user. Matching order:
      1. (sso_provider, sso_provider_id) — durable across email changes
      2. (organization_id, email) — links SSO to an existing password user
      3. New user with JIT-provisioned admin role if org has no users yet,
         otherwise ap_clerk (least-privilege default).
    """
    # 1. Durable match
    result = await db.execute(
        select(User).where(
            User.sso_provider == provider,
            User.sso_provider_id == sub,
            User.organization_id == org.id,
        )
    )
    user = result.scalar_one_or_none()

    # 2. Link by email — first SSO login for an existing password user
    if user is None:
        result = await db.execute(
            select(User).where(User.email == email, User.organization_id == org.id)
        )
        user = result.scalar_one_or_none()
        if user is not None:
            user.sso_provider = provider
            user.sso_provider_id = sub
            logger.info("Linked SSO (%s) to existing user %s", provider, email)

    # 3. Create new
    if user is None:
        full_name = (
            claims.get("name")
            or f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip()
            or email.split("@", 1)[0]
        )

        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=full_name,
            sso_provider=provider,
            sso_provider_id=sub,
            hashed_password=None,  # SSO-only
            is_active=True,
            organization_id=org.id,
            must_change_password=False,
        )
        db.add(user)
        await db.flush()

        # Assign least-privilege role by default. Admins can elevate via
        # the admin UI after first login. If this is the very first user
        # in the org (unlikely via SSO but possible via SCIM), grant admin.
        first_user = (
            await db.execute(
                select(User.id)
                .where(User.organization_id == org.id, User.id != user.id)
                .limit(1)
            )
        ).scalar_one_or_none() is None

        role_name = "admin" if first_user else "ap_clerk"
        role = (
            await db.execute(select(Role).where(Role.name == role_name))
        ).scalar_one_or_none()
        if role is not None:
            db.add(UserRole(user_id=user.id, role_id=role.id))

        logger.info("JIT-provisioned user %s in org %s as %s", email, org.slug, role_name)

    # Ensure roles eager-loaded for any downstream caller
    await db.execute(
        select(User).where(User.id == user.id).options(selectinload(User.roles))
    )
    return user
