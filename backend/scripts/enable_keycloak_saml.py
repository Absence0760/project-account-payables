"""Point a tenant at the local Keycloak SAML IdP (or turn it back off).

The SAML companion to ``enable_keycloak_sso.py``. SAML SP config is per-tenant
and lives in ``Organization.settings.sso`` (protocol="saml") on the control DB.

Unlike the OIDC client_secret, the IdP's SAML *signing certificate* is generated
by Keycloak on realm import and changes every boot, so we can't hardcode it —
this script fetches the live cert + endpoints from Keycloak's SAML descriptor at
import time. The descriptor URL is fixed (localhost:8088), not user-supplied, so
there's no SSRF surface; parsing uses python3-saml's DTD/entity-hardened parser.

Usage (from ``backend/`` with the venv active, after ``pnpm idp:up``):

    python scripts/enable_keycloak_saml.py              # enable on acme
    python scripts/enable_keycloak_saml.py --slug techflow
    python scripts/enable_keycloak_saml.py --disable    # turn it back off

After enabling, the acme login page (http://acme.localhost:7777) renders the
"Sign in with SSO" button. Log in as demo@acme.com / demo (a Keycloak user that
mirrors the seeded admin, so the first SAML login links to the existing row).

The shared SP entityId + ACS MUST match the SAML client in the realm export
(backend/keycloak/realm-export.json). One Keycloak client serves every tenant;
the backend recovers the tenant from the server-minted RelayState.
"""

from __future__ import annotations

import argparse
import asyncio

from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser
from sqlalchemy import select

from app.database import control_engine, control_session_factory
from app.models.organization import Organization

# Keycloak's SAML IdP metadata (the SAML analog of the OIDC discovery doc). The
# signing cert + SSO URL + entityId are read from here every run.
KEYCLOAK_SAML_DESCRIPTOR_URL = "http://localhost:8088/realms/feohledger/protocol/saml/descriptor"

# Shared local SP entityId — MUST equal the SAML client's clientId in
# realm-export.json (one client for all local tenants; tenant comes from
# RelayState, so the SP id can be shared).
LOCAL_SAML_SP_ENTITY_ID = "http://localhost:8000/api/auth/saml/metadata"


def _fetch_idp_config() -> dict:
    """Pull entityId / SSO URL / signing cert from Keycloak's live descriptor."""
    parsed = OneLogin_Saml2_IdPMetadataParser.parse_remote(
        KEYCLOAK_SAML_DESCRIPTOR_URL, validate_cert=False
    )
    idp = parsed.get("idp") or {}
    entity_id = idp.get("entityId")
    sso_url = (idp.get("singleSignOnService") or {}).get("url")
    cert = idp.get("x509cert")
    if not (entity_id and sso_url and cert):
        raise SystemExit(
            "Could not read entityId/SSO URL/signing cert from Keycloak's SAML "
            f"descriptor at {KEYCLOAK_SAML_DESCRIPTOR_URL}. Is `pnpm idp:up` running?"
        )
    return {
        "idp_entity_id": entity_id,
        "idp_sso_url": sso_url,
        "idp_x509_cert": cert,
    }


async def _apply(slug: str, disable: bool) -> None:
    sso_block = None
    if not disable:
        idp = _fetch_idp_config()
        sso_block = {
            "enabled": True,
            "protocol": "saml",
            "provider": "saml",
            "sp_entity_id": LOCAL_SAML_SP_ENTITY_ID,
            **idp,
            # Empty allowlist = accept any email domain. Add "acme.com" here to
            # exercise the JIT domain-allowlist path in app/api/auth_saml.py.
            "allowed_email_domains": [],
        }

    async with control_session_factory() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if org is None:
            raise SystemExit(
                f"No organization with slug {slug!r}. Run `pnpm seed` first, "
                "or pass --slug <existing-tenant>."
            )

        # Reassign a fresh dict so SQLAlchemy flags the JSONB column dirty.
        new_settings = dict(org.settings or {})
        if disable:
            new_settings.pop("sso", None)
            action = "disabled"
        else:
            new_settings["sso"] = sso_block
            action = "enabled"
        org.settings = new_settings
        await session.commit()

    print(f"Keycloak SAML {action} for tenant {slug!r}.")
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
