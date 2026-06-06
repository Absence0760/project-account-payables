"""Point a tenant at the local Keycloak IdP (or turn it back off).

This is the app-side companion to the `keycloak` Docker Compose service
(see ``docker-compose.yml`` / ``keycloak/realm-export.json``). SSO config is
per-tenant and lives in ``Organization.settings.sso`` on the control-plane DB,
not in env — so enabling local SSO is a one-row patch, not a restart.

Usage (from ``backend/`` with the venv active, after ``pnpm idp:up``):

    python scripts/enable_keycloak_sso.py              # enable on acme
    python scripts/enable_keycloak_sso.py --slug techflow
    python scripts/enable_keycloak_sso.py --disable    # turn it back off

After enabling, the acme login page (http://acme.localhost:7777) renders the
"Sign in with SSO" button. Log in as demo@acme.com / demo (a Keycloak user that
mirrors the seeded admin, so the first SSO login links to the existing row).

The values here MUST match the realm export: client id, client secret, and the
discovery URL of the `account-payables` realm. localhost:8088 is reachable from
the backend process (run on the host, not in a container) and from the browser.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.database import control_engine, control_session_factory
from app.models.organization import Organization

KEYCLOAK_SSO_CONFIG = {
    "enabled": True,
    "provider": "oidc",
    "discovery_url": (
        "http://localhost:8088/realms/account-payables/.well-known/openid-configuration"
    ),
    "client_id": "account-payables-app",
    "client_secret": "local-dev-keycloak-secret",
    # Empty allowlist = accept any email domain. Add "acme.com" here to exercise
    # the JIT domain-allowlist path in app/api/auth_sso.py.
    "allowed_email_domains": [],
}


async def _apply(slug: str, disable: bool) -> None:
    async with control_session_factory() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if org is None:
            raise SystemExit(
                f"No organization with slug {slug!r}. Run `pnpm seed` first, "
                "or pass --slug <existing-tenant>."
            )

        # Reassign a fresh dict so SQLAlchemy flags the JSONB column dirty
        # (the column has no MutableDict wrapper, so in-place edits don't track).
        new_settings = dict(org.settings or {})
        if disable:
            new_settings.pop("sso", None)
            action = "disabled"
        else:
            new_settings["sso"] = dict(KEYCLOAK_SSO_CONFIG)
            action = "enabled"
        org.settings = new_settings
        await session.commit()

    print(f"Keycloak SSO {action} for tenant {slug!r}.")
    if not disable:
        print(f"  Login page: http://{slug}.localhost:7777")
        print("  IdP user:   demo@acme.com / demo  (links to the seeded admin)")
        print("  Or:         newhire@acme.com / demo  (JIT-provisions a new ap_clerk)")


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slug", default="acme", help="Tenant slug to (un)configure (default: acme)"
    )
    parser.add_argument(
        "--disable", action="store_true", help="Remove the SSO block instead of adding it"
    )
    args = parser.parse_args()
    try:
        await _apply(args.slug, args.disable)
    finally:
        await control_engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
