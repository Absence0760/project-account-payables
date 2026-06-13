"""Provider-neutral identity provisioning shared by every SSO protocol.

Both the OIDC callback (`api/auth_sso.py`) and the SAML ACS
(`api/auth_saml.py`) land here once they have a verified
`(provider, subject, email)` tuple from the IdP. Keeping JIT provisioning
and the email-domain allowlist in one place means the two protocols share
the exact same user-matching, role-defaulting, and SSO-linking semantics —
the protocol-specific code only differs in how it *verifies* the IdP
response, never in how it maps that response onto a `User`.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.organization import Organization
from app.models.user import Role, User, UserRole

logger = logging.getLogger(__name__)


class EmailDomainNotAllowed(ValueError):
    """Raised when a verified IdP email falls outside the tenant's allowlist.

    Carries the normalized email so the OIDC caller can preserve its existing
    audit detail; the SAML caller deliberately drops it (PII-out-of-logs).
    """

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__("Email domain is not allowed for this workspace.")


def extract_and_check_email(email_raw: str, allowed_email_domains: list[str]) -> str:
    """Normalize an IdP-supplied email and enforce the optional domain allowlist.

    Returns the lower-cased, stripped email. Raises `EmailDomainNotAllowed`
    (carrying the normalized email) when an allowlist is configured and the
    email's domain isn't on it. An empty allowlist means "any domain".
    """
    email = email_raw.lower().strip()
    if allowed_email_domains:
        domain = email.rsplit("@", 1)[-1]
        if domain not in allowed_email_domains:
            raise EmailDomainNotAllowed(email)
    return email


async def jit_provision(
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

    Note on provider transitions: the durable key is (sso_provider,
    sso_provider_id), so a tenant that switches a user from OIDC
    (provider="okta"/"entra") to SAML (provider="saml", subject=NameID)
    won't match on step 1 the first time — it falls to the email-link
    branch (step 2), which rebinds sso_provider/sso_provider_id to the new
    protocol. Intentional and deterministic; documented in
    docs/authentication.md.
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
                select(User.id).where(User.organization_id == org.id, User.id != user.id).limit(1)
            )
        ).scalar_one_or_none() is None

        role_name = "admin" if first_user else "ap_clerk"
        role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
        if role is not None:
            db.add(UserRole(user_id=user.id, role_id=role.id))

        logger.info("JIT-provisioned user %s in org %s as %s", email, org.slug, role_name)

    # Ensure roles eager-loaded for any downstream caller
    await db.execute(select(User).where(User.id == user.id).options(selectinload(User.roles)))
    return user
