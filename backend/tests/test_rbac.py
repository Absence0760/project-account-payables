"""RBAC tests — `require_roles` semantics + coverage check that no API
endpoint regresses to "no auth dependency" once added.

The coverage check is the more important of the two: the dependency itself
is small and easy to reason about, but adding a new router endpoint without
any auth is precisely the kind of mistake that put us in this hole to begin
with. The test catches it at PR time, not after a customer hits the endpoint.
"""

from __future__ import annotations

import inspect
import re
import uuid
from collections.abc import Iterable
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api import (
    admin,
    auth,
    auth_sso,
    cards,
    dashboard,
    erp_webhook,
    exceptions,
    gl_accounts,
    invoices,
    organization,
    payments,
    purchase_orders,
    scim,
    signup,
    vendors,
    workflow,
    workflow_definitions,
)
from app.api.deps import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_current_user,
    require_roles,
)

# ---------- Endpoints that legitimately don't take a JWT --------------------

# Login / MFA-challenge / SSO callbacks / SCIM (own bearer auth) /
# signup / webhooks / health. These are explicitly excluded from the
# "must have auth" coverage check.
NO_AUTH_REQUIRED = {
    # auth_sso.py — login flow
    ("GET", "/auth/sso/config"),
    ("GET", "/auth/sso/authorize"),
    ("POST", "/auth/sso/callback"),
    # auth.py — pre-login + MFA challenge
    ("POST", "/auth/login"),
    ("POST", "/auth/mfa/challenge/email"),
    ("POST", "/auth/mfa/verify"),
    ("POST", "/auth/logout"),  # uses Bearer header but not as a Depends
    # cards.py — webhook authenticated by provider signature
    ("POST", "/cards/webhook/{provider}"),
    # payments.py — payment-processor webhook; HMAC-verified, tenant in URL path
    ("POST", "/payments/webhook/{tenant_slug}/{provider}"),
    # erp_webhook.py
    ("POST", "/erp/webhook/{erp_type}"),
    # signup.py — public
    ("GET", "/signup/slug-check"),
    ("POST", "/signup/start"),
    ("POST", "/signup/complete"),
    # SCIM — per-tenant bearer token, validated inside handlers
    ("GET", "/scim/v2/Users"),
    ("GET", "/scim/v2/Users/{user_id}"),
    ("POST", "/scim/v2/Users"),
    ("PATCH", "/scim/v2/Users/{user_id}"),
    ("DELETE", "/scim/v2/Users/{user_id}"),
    ("GET", "/scim/v2/ServiceProviderConfig"),
    ("GET", "/scim/v2/Schemas/{schema_id}"),
}

# Routers wired into the app at /api — same set as app/main.py.
ROUTERS = [
    admin.router,
    auth.router,
    auth_sso.router,
    cards.router,
    dashboard.router,
    erp_webhook.router,
    exceptions.router,
    gl_accounts.router,
    invoices.router,
    organization.router,
    payments.router,
    purchase_orders.router,
    scim.router,
    signup.router,
    vendors.router,
    workflow.router,
    workflow_definitions.router,
]


def _iter_endpoints(routers: Iterable) -> Iterable[tuple[str, str, callable]]:
    for r in routers:
        for route in r.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods or ():
                if method == "HEAD":
                    continue
                yield method, route.path, route.endpoint


def _has_auth_dep(endpoint: callable) -> bool:
    """True if the endpoint signature includes a `Depends(get_current_user)`
    or `Depends(require_roles(...))` parameter."""
    sig = inspect.signature(endpoint)
    for p in sig.parameters.values():
        default = p.default
        # FastAPI Depends instances expose a `.dependency` attribute pointing
        # at the callable. Every protected endpoint should ultimately resolve
        # through `get_current_user`.
        dep = getattr(default, "dependency", None)
        if dep is None:
            continue
        if dep is get_current_user:
            return True
        # require_roles returns a closure named "checker" that itself
        # depends on get_current_user. Detect either by name or by
        # walking the closure's cellvars.
        if getattr(dep, "__name__", "") == "checker":
            return True
    return False


def test_every_endpoint_requires_auth_or_is_explicitly_public():
    """Coverage gate: a new router endpoint must either ship with an auth
    dependency or be added to NO_AUTH_REQUIRED. This is the *one* test that
    has to fail noisily in CI if someone forgets RBAC."""
    missing: list[str] = []
    for method, path, endpoint in _iter_endpoints(ROUTERS):
        if (method, path) in NO_AUTH_REQUIRED:
            continue
        if not _has_auth_dep(endpoint):
            missing.append(f"{method} {path} ({endpoint.__module__}.{endpoint.__name__})")
    assert not missing, "Endpoints without auth dependency:\n  " + "\n  ".join(missing)


def test_no_auth_required_paths_actually_exist():
    """Don't let NO_AUTH_REQUIRED rot — every entry should match a real
    route. A renamed endpoint that no longer exists silently weakens the
    coverage gate above."""
    seen = {(m, p) for m, p, _ in _iter_endpoints(ROUTERS)}
    stale = NO_AUTH_REQUIRED - seen
    assert not stale, f"NO_AUTH_REQUIRED has entries that no longer exist: {sorted(stale)}"


# ---------- require_roles unit tests ----------------------------------------


def _fake_user(*roles: str) -> MagicMock:
    user = MagicMock(spec=["id", "organization_id", "roles"])
    user.id = uuid.uuid4()
    user.organization_id = uuid.uuid4()
    user.roles = [SimpleNamespace(name=r) for r in roles]
    return user


def _fake_request() -> MagicMock:
    req = MagicMock()
    req.method = "POST"
    req.url.path = "/api/test"
    return req


@pytest.mark.asyncio
async def test_require_roles_accepts_matching_role():
    checker = require_roles(ROLE_ADMIN)
    user = _fake_user(ROLE_ADMIN)
    result = await checker(request=_fake_request(), user=user)
    assert result is user


@pytest.mark.asyncio
async def test_require_roles_accepts_any_of_listed_roles():
    """A user with multiple roles passes if they hold at least one."""
    checker = require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)
    user = _fake_user(ROLE_AP_MANAGER, ROLE_AP_CLERK)
    assert (await checker(request=_fake_request(), user=user)) is user


@pytest.mark.asyncio
async def test_require_roles_rejects_clerk_only():
    checker = require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)
    user = _fake_user(ROLE_AP_CLERK)
    with pytest.raises(HTTPException) as exc:
        await checker(request=_fake_request(), user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_roles_rejects_user_with_no_roles():
    checker = require_roles(ROLE_ADMIN)
    user = _fake_user()
    with pytest.raises(HTTPException) as exc:
        await checker(request=_fake_request(), user=user)
    assert exc.value.status_code == 403


def test_require_roles_rejects_empty_role_list_at_import_time():
    with pytest.raises(ValueError):
        require_roles()


def test_require_roles_rejects_typo_at_import_time():
    """Catches 'admni' before it ships — typos otherwise silently lock everyone out."""
    with pytest.raises(ValueError):
        require_roles("admni")


@pytest.mark.asyncio
async def test_require_roles_logs_denials(caplog):
    """Denials should hit the WARN-level log so monitoring can detect probing."""
    import logging

    caplog.set_level(logging.WARNING, logger="app.api.deps")
    checker = require_roles(ROLE_ADMIN)
    user = _fake_user(ROLE_AP_CLERK)
    with pytest.raises(HTTPException):
        await checker(request=_fake_request(), user=user)
    assert any("RBAC denied" in rec.message for rec in caplog.records)


# ---------- Sanity check on role constants ---------------------------------


def test_all_roles_constant_matches_seed_script():
    """ALL_ROLES drives `require_roles` typo-detection. Keep it in sync with
    `scripts/seed.py`'s role list — if a new role is added there but not here,
    `require_roles("new_role")` would explode at import time and lock that
    role out of every protected endpoint."""
    from pathlib import Path

    seed_path = Path(__file__).resolve().parent.parent / "scripts" / "seed.py"
    text = seed_path.read_text()
    # Heuristic: pull every "name=\"role_name\"" inside Role(...) constructions.
    seeded = set(re.findall(r'Role\([^)]*name="([^"]+)"', text))
    if not seeded:
        pytest.skip("Could not parse seed.py role list — heuristic missed")
    missing = seeded - set(ALL_ROLES)
    extra = set(ALL_ROLES) - seeded
    assert not missing, f"Roles seeded but not in ALL_ROLES: {missing}"
    # `extra` is OK — ALL_ROLES is the canonical list, seed may not enumerate all
    _ = extra
