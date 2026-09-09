"""Set the local Authentik SCIM bearer token on a tenant (or clear it).

This is the app-side companion to the `authentik-*` Docker Compose services
(see ``docker-compose.yml`` / ``authentik/blueprints/``). Authentik is the SCIM
*client*: it pushes users into our SCIM Service Provider (``app/api/scim.py``,
``/api/scim/v2/Users``) authenticating with a per-tenant bearer token. The app
resolves the tenant from the token's sha256 (``Organization.scim_bearer_hash``,
mirrored onto ``settings.sso.scim_bearer_hash``).

Unlike the admin "mint SCIM token" endpoint — which generates a random token and
shows it once — this sets a FIXED, known token so it can match the static value
baked into the Authentik blueprint. Local dev only; never use a fixed token in a
deployed environment.

Usage (from ``backend/`` with the venv active):

    python scripts/enable_authentik_scim.py            # set on acme
    python scripts/enable_authentik_scim.py --slug techflow
    python scripts/enable_authentik_scim.py --disable  # clear the token

The plaintext token MUST equal the `token:` in
``authentik/blueprints/feohledger-scim.yaml``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

from sqlalchemy import select

# Anchor THIS checkout's `backend/` on sys.path before `app` is imported below.
# Run as a script, sys.path[0] is `scripts/`, so `app` is not on sys.path at
# all — and a worktree reusing the primary checkout's `backend/.venv` resolves
# it through the editable install's baked-in `__editable___backend_0_1_0_finder`,
# acting on the WRONG tree with nothing on screen to say so. Inserted at 1 so
# the script's own directory keeps priority; idempotent, and a no-op in the
# checkout the venv was installed from.
#
# Deliberately NOT hoisted into a variable: ruff's E402 exempts `sys.path`
# manipulation before imports but not an assignment beside it, so naming the
# path here turns every import below into a lint error.
# See `frontend/tests-e2e/README.md` § Running from a worktree.
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(1, str(Path(__file__).resolve().parent.parent))

from app.database import control_engine, control_session_factory
from app.models.organization import Organization

# Keep in lockstep with authentik/blueprints/feohledger-scim.yaml (token:).
SCIM_TOKEN = "local-dev-scim-token-acme"


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

        new_settings = dict(org.settings or {})
        sso = dict(new_settings.get("sso") or {})
        if disable:
            sso.pop("scim_bearer_hash", None)
            new_settings["sso"] = sso
            org.settings = new_settings
            org.scim_bearer_hash = None
            action = "cleared"
        else:
            digest = hashlib.sha256(SCIM_TOKEN.encode("utf-8")).hexdigest()
            sso["scim_bearer_hash"] = digest  # mirror, kept for audit/log parity
            new_settings["sso"] = sso
            org.settings = new_settings
            # The indexed column is the authoritative lookup for SCIM auth.
            org.scim_bearer_hash = digest
            action = "set"
        await session.commit()

    print(f"Authentik SCIM token {action} for tenant {slug!r}.")
    if not disable:
        print(f"  Token (matches the Authentik blueprint): {SCIM_TOKEN}")
        print("  Authentik admin: http://localhost:9002  (akadmin / admin)")
        print("  Then: Providers -> 'FeohLedger SCIM' -> Run sync")


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slug", default="acme", help="Tenant slug to (un)configure (default: acme)"
    )
    parser.add_argument(
        "--disable", action="store_true", help="Clear the SCIM token instead of setting it"
    )
    args = parser.parse_args()
    try:
        await _apply(args.slug, args.disable)
    finally:
        await control_engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
