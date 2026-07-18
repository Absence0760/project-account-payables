"""RBAC tests — `require_roles` semantics + coverage check that no API
endpoint regresses to "no auth dependency" once added.

The coverage check is the more important of the two: the dependency itself
is small and easy to reason about, but adding a new router endpoint without
any auth is precisely the kind of mistake that put us in this hole to begin
with. The test catches it at PR time, not after a customer hits the endpoint.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Iterable
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api.deps import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_api_key_principal,
    get_current_user,
    require_roles,
)
from app.api.portal_deps import get_current_vendor_user
from app.main import app

# ---------- Endpoints that legitimately don't take a JWT --------------------

# Login / MFA-challenge / SSO callbacks / SCIM (own bearer auth) /
# signup / webhooks / health. These are explicitly excluded from the
# "must have auth" coverage check.
NO_AUTH_REQUIRED = {
    # auth_sso.py — login flow
    ("GET", "/auth/sso/config"),
    ("GET", "/auth/sso/authorize"),
    ("POST", "/auth/sso/callback"),
    # auth_saml.py — SAML SSO login flow (IdP-driven; no app session yet)
    ("GET", "/auth/saml/config"),
    ("GET", "/auth/saml/login"),
    ("POST", "/auth/saml/acs"),
    ("POST", "/auth/saml/exchange"),
    ("GET", "/auth/saml/metadata"),
    # auth.py — pre-login + MFA challenge
    ("POST", "/auth/login"),
    ("POST", "/auth/mfa/challenge/email"),
    ("POST", "/auth/mfa/verify"),
    # WebAuthn / passkey LOGIN ceremony — gated by the login-issued MFA
    # challenge token (the same pre-access-token credential as /mfa/verify),
    # not a JWT. The register/list/delete passkey endpoints DO require JWT.
    ("POST", "/auth/mfa/passkey/authenticate"),
    ("POST", "/auth/mfa/passkey/authenticate/verify"),
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
    ("PUT", "/scim/v2/Users/{user_id}"),
    ("PATCH", "/scim/v2/Users/{user_id}"),
    ("DELETE", "/scim/v2/Users/{user_id}"),
    ("GET", "/scim/v2/Groups"),
    ("GET", "/scim/v2/Groups/{group_id}"),
    ("POST", "/scim/v2/Groups"),
    ("PUT", "/scim/v2/Groups/{group_id}"),
    ("PATCH", "/scim/v2/Groups/{group_id}"),
    ("DELETE", "/scim/v2/Groups/{group_id}"),
    ("GET", "/scim/v2/ServiceProviderConfig"),
    ("GET", "/scim/v2/Schemas/{schema_id}"),
    # portal_auth.py — pre-login + logout (Bearer header, not as Depends)
    ("POST", "/portal/auth/login"),
    ("POST", "/portal/auth/logout"),
    # portal_auth.py — MFA challenge verify + email-OTP backup request; the
    # login-issued challenge token (typ=vendor_mfa_challenge) IS the credential,
    # so both are pre-access-token (public-by-design).
    ("POST", "/portal/auth/mfa/challenge"),
    ("POST", "/portal/auth/mfa/challenge/email"),
    # portal.py — single-use card-reveal token; the URL token IS the credential
    ("GET", "/portal/cards/{token}"),
    # portal.py — public-by-design white-label brand read for the (unauthenticated)
    # supplier-portal login + authed portal pages. Tenant resolved by `get_tenant`
    # (X-Tenant-Slug / custom-domain Host); returns only the non-sensitive
    # `BrandConfig` fields. Mirrors /auth/sso/config. See docs/white-label.md.
    ("GET", "/portal/branding"),
    # email_intake.py — webhook authenticated by HMAC + per-tenant token in address
    ("POST", "/email-intake/inbound/{provider}"),
    # peppol_inbound.py — inbound AS4 receive webhook; HMAC-verified, tenant in
    # URL path, public-by-design (an external Access Point calls it).
    ("POST", "/peppol/inbound/{tenant_slug}"),
    # catalogs.py public_router — punch-out cart return (PunchOutOrderMessage);
    # shared-secret HMAC-verified + BuyerCookie-correlated, tenant in URL path,
    # public-by-design (the supplier / buyer browser POSTs it).
    ("POST", "/catalogs/punchout/return/{tenant_slug}"),
    # email_actions.py — approve/reject from the assigned-invoice email; the
    # signed single-action token in the URL IS the credential (no JWT/session).
    ("GET", "/invoices/email-action/{token}"),
    ("POST", "/invoices/email-action/{token}/confirm"),
    # slack_approvals.py — approve/reject from the Slack message buttons; gated by
    # the Slack request signature (HMAC) + the signed single-use action token in
    # the button value (no JWT/session). Public-by-design (Slack POSTs it).
    ("POST", "/approvals/slack/interactivity"),
    # teams_approvals.py — approve/reject from the Teams approval card; gated by
    # the Teams Outgoing-Webhook HMAC + the signed single-use action token in the
    # card action (no JWT/session). Public-by-design (Teams POSTs it).
    ("POST", "/approvals/teams/interactivity"),
    # billing_webhook.py — inbound billing-provider (Stripe) webhook; HMAC-verified
    # inside the adapter's parse_webhook + deduped by event id, provider in URL
    # path. Public-by-design (the billing provider POSTs it); resolves the
    # control-plane Subscription by the provider id carried in the event.
    ("POST", "/billing/webhook/{provider}"),
    # main.py — liveness probe. No identity, no tenant data.
    ("GET", "/health"),
    # main.py — non-secret config (hCaptcha sitekey, tenant URL template) the
    # frontend needs before a session exists (signup form, tenant lookup).
    ("GET", "/public-config"),
    # v1_openapi.py — the published OpenAPI contract + Swagger UI for the public
    # /api/v1 surface. Public-to-read like any API doc page; 404s when
    # AP_PUBLIC_API_ENABLED is off (see _ensure_enabled). No tenant data.
    ("GET", "/v1/openapi.json"),
    ("GET", "/v1/docs"),
}


def _iter_api_routes() -> Iterable[tuple[str, set[str], APIRoute]]:
    """Every `APIRoute` mounted under `/api` in the live app, with its full
    method set and path.

    FastAPI 0.139 keeps routers pulled in via `app.include_router(...)` as
    nested objects on `app.routes` rather than flattening their routes into
    top-level `APIRoute`s, so a naive `for route in app.routes` scan silently
    misses everything mounted through a sub-router. `iter_route_contexts` is
    the supported way to flatten the nested router tree into (path, methods,
    route) tuples — see `test_sod_endpoint_wiring.py` for the same pattern.
    """
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # pragma: no cover - pre-0.139 FastAPI fallback
        for route in app.routes:
            if isinstance(route, APIRoute):
                yield route.path, route.methods or set(), route
        return
    for ctx in iter_route_contexts(app.routes):
        if isinstance(ctx.route, APIRoute):
            yield ctx.path, ctx.methods or set(), ctx.route


def _iter_endpoints() -> Iterable[tuple[str, str, callable]]:
    """(method, path, endpoint) for every `/api` endpoint in the live app.

    Introspects the running `FastAPI` app instead of a hand-maintained router
    list — a new `app.include_router(...)` in `app/main.py` is automatically
    picked up here, so this coverage gate can't silently go blind to a router
    the way the old list did. `/docs`, `/redoc`, `/openapi.json` (FastAPI's own
    auto-generated meta routes, mounted outside `/api`) are excluded; the path
    is stripped of its leading `/api` to match the existing NO_AUTH_REQUIRED /
    _PERMISSION_GATED_ENDPOINTS path convention.
    """
    for path, methods, route in _iter_api_routes():
        if not path.startswith("/api"):
            continue
        stripped = path[len("/api") :] or "/"
        for method in methods:
            if method == "HEAD":
                continue
            yield method, stripped, route.endpoint


def _has_auth_dep(endpoint: callable) -> bool:
    """True if the endpoint signature includes a `Depends(get_current_user)`,
    `Depends(require_roles(...))`, or `Depends(get_current_vendor_user)`
    parameter."""
    sig = inspect.signature(endpoint)
    for p in sig.parameters.values():
        default = p.default
        dep = getattr(default, "dependency", None)
        if dep is None:
            continue
        if dep is get_current_user or dep is get_current_vendor_user:
            return True
        # Programmatic /api/v1 routes authenticate with an API key, not a JWT.
        if dep is get_api_key_principal:
            return True
        # require_roles AND require_api_scope each return a closure named
        # "checker" that itself depends on get_current_user / the api-key dep.
        if getattr(dep, "__name__", "") == "checker":
            return True
    return False


def test_every_endpoint_requires_auth_or_is_explicitly_public():
    """Coverage gate: a new router endpoint must either ship with an auth
    dependency or be added to NO_AUTH_REQUIRED. This is the *one* test that
    has to fail noisily in CI if someone forgets RBAC."""
    missing: list[str] = []
    for method, path, endpoint in _iter_endpoints():
        if (method, path) in NO_AUTH_REQUIRED:
            continue
        if not _has_auth_dep(endpoint):
            missing.append(f"{method} {path} ({endpoint.__module__}.{endpoint.__name__})")
    assert not missing, "Endpoints without auth dependency:\n  " + "\n  ".join(missing)


def test_no_auth_required_paths_actually_exist():
    """Don't let NO_AUTH_REQUIRED rot — every entry should match a real
    route. A renamed endpoint that no longer exists silently weakens the
    coverage gate above."""
    seen = {(m, p) for m, p, _ in _iter_endpoints()}
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


# ---------- Permission gating on the split sensitive endpoints --------------

# The endpoints migrated off `require_roles` onto `require_permission` — the
# fraud-sensitive, SoD-splittable set. Each must carry a permission-backed
# `checker` whose closure cell references the catalog permission(s) it needs.
# (method, path) → the permission expected on the gate.
from app.api import permissions as perm_catalog  # noqa: E402

_PERMISSION_GATED_ENDPOINTS = {
    ("POST", "/payments/runs"): perm_catalog.PERM_PAYMENT_RUN_APPROVE,
    ("POST", "/payments/runs/{run_id}/execute"): perm_catalog.PERM_PAYMENT_EXECUTE,
    ("POST", "/payments/runs/{run_id}/resume"): perm_catalog.PERM_PAYMENT_EXECUTE,
    ("POST", "/payments/{payment_id}/void"): perm_catalog.PERM_PAYMENT_VOID,
    ("POST", "/vendors/change-requests/{request_id}/approve"): (
        perm_catalog.PERM_VENDOR_BANK_CHANGE_APPROVE
    ),
    ("POST", "/vendors/{vendor_id}/block"): perm_catalog.PERM_VENDOR_BLOCK,
    ("POST", "/vendors/{vendor_id}/unblock"): perm_catalog.PERM_VENDOR_BLOCK,
    ("POST", "/vendors"): perm_catalog.PERM_VENDOR_MANAGE,
    ("PATCH", "/vendors/{vendor_id}"): perm_catalog.PERM_VENDOR_MANAGE,
    ("POST", "/vendors/{vendor_id}/verify"): perm_catalog.PERM_VENDOR_MANAGE,
    ("POST", "/vendors/{vendor_id}/reject"): perm_catalog.PERM_VENDOR_MANAGE,
    ("POST", "/admin/users"): perm_catalog.PERM_USER_MANAGE,
    ("PATCH", "/admin/users/{user_id}"): perm_catalog.PERM_USER_MANAGE,
    ("DELETE", "/admin/users/{user_id}"): perm_catalog.PERM_USER_MANAGE,
    ("POST", "/admin/users/bulk-delete"): perm_catalog.PERM_USER_MANAGE,
}


def _gate_permissions(endpoint: callable) -> set[str] | None:
    """Return the set of permission strings a `require_permission(...)` gate on
    this endpoint requires, or None if the endpoint isn't permission-gated.

    `require_permission` closes over `needed_set` (a frozenset of permission
    strings); read it out of the `checker` closure's cells so the test verifies
    the *actual* gate, not just that some auth dep exists."""
    sig = inspect.signature(endpoint)
    for p in sig.parameters.values():
        dep = getattr(p.default, "dependency", None)
        if dep is None or getattr(dep, "__name__", "") != "checker":
            continue
        closure = getattr(dep, "__closure__", None) or ()
        for cell in closure:
            val = cell.cell_contents
            if isinstance(val, frozenset) and val and all(isinstance(v, str) for v in val):
                # The require_roles checker also closes over a frozenset
                # (allowed roles); distinguish by membership in the catalog.
                if val <= set(perm_catalog.ALL_PERMISSIONS):
                    return set(val)
    return None


def test_split_endpoints_are_permission_gated():
    """Every migrated sensitive endpoint gates on the expected catalog
    permission via `require_permission` — not just on `require_roles`. This is
    the SoD enforcement contract; a regression that reverts one of these to a
    role gate (or drops the permission) fails here."""
    by_path = {(m, p): ep for m, p, ep in _iter_endpoints()}
    failures: list[str] = []
    for key, expected_perm in _PERMISSION_GATED_ENDPOINTS.items():
        endpoint = by_path.get(key)
        if endpoint is None:
            failures.append(f"{key[0]} {key[1]} — route no longer exists")
            continue
        gate = _gate_permissions(endpoint)
        if gate is None:
            failures.append(
                f"{key[0]} {key[1]} — not permission-gated (reverted to require_roles?)"
            )
        elif expected_perm not in gate:
            failures.append(f"{key[0]} {key[1]} — gate {sorted(gate)} missing {expected_perm!r}")
    assert not failures, "Permission-gate regressions:\n  " + "\n  ".join(failures)


# ---------- Sanity check on role constants ---------------------------------


def test_all_roles_constant_matches_seed_script():
    """ALL_ROLES drives `require_roles` typo-detection and must stay in
    lockstep with the roles `scripts/seed.py` actually creates. seed.py builds
    its Role rows from ``ROLE_DEFINITIONS`` — keyed by the same ROLE_*
    constants — so the two sets must be identical: a role in ALL_ROLES that
    seed never creates would 403 every real user, and a role seed creates that
    ALL_ROLES omits would make ``require_roles(<it>)`` explode at import time
    and lock that role out of every protected endpoint.

    Asserting against the imported ``ROLE_DEFINITIONS`` (not a regex over the
    source) means this check can never silently skip when seed.py's formatting
    changes — the previous heuristic could, turning the guard into a no-op."""
    import scripts.seed as seed_module

    seeded = set(seed_module.ROLE_DEFINITIONS)
    missing = seeded - set(ALL_ROLES)
    extra = set(ALL_ROLES) - seeded
    assert not missing, f"Roles seeded but not in ALL_ROLES: {missing}"
    assert not extra, f"Roles in ALL_ROLES but never seeded: {extra}"
