"""Endpoint SoD wiring — the splittable, money/approval-sensitive routes gate on
`require_permission(...)`, not bare `require_roles(...)`.

Why this matters: the granular-permission layer exists so an org can SPLIT
fraud-sensitive duties (approve invoices vs. move money) onto custom roles. A
route still on `require_roles` silently ignores that split — a custom role that
had `payment.execute` stripped could still move money, and a custom role granted
`invoice.approve` could not actually approve. These tests pin the wiring so a
future refactor can't quietly drop a route back onto `require_roles`.

We resolve each route's `require_permission` checker out of its FastAPI
dependency tree and exercise it directly with a user holding / lacking the
permission — so the test covers BOTH "this route is permission-gated" and "it
gates on the RIGHT permission," with no DB.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.permissions import (
    PERM_INVOICE_APPROVE,
    PERM_PAYMENT_EXECUTE,
    PERM_PAYMENT_RUN_APPROVE,
)
from app.main import app


def _iter_app_routes():
    """Yield (path, methods, route) for every route in the app.

    FastAPI 0.138 changed `include_router` to keep nested `_IncludedRouter`
    objects in `app.routes` instead of flattening sub-routes into top-level
    `APIRoute`s, so the old flat scan no longer sees included routes. Flatten
    via the supported `iter_route_contexts` helper (full path + the underlying
    `APIRoute`, which still carries `.dependant`); fall back to the flat list on
    a FastAPI that predates the helper.
    """
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:
        for route in app.routes:
            yield getattr(route, "path", None), getattr(route, "methods", set()) or set(), route
        return
    for ctx in iter_route_contexts(app.routes):
        yield ctx.path, ctx.methods or set(), ctx.route


def _find_route(path: str, method: str):
    for route_path, methods, route in _iter_app_routes():
        if route_path == path and method in methods:
            return route
    raise AssertionError(f"route not found: {method} {path}")


def _permission_checkers(route):
    """Every `require_permission(...).checker` reachable from a route, with the
    permission set it captured in its closure."""
    found: list[frozenset[str]] = []

    def walk(dep):
        call = getattr(dep, "call", None)
        if call is not None and getattr(call, "__qualname__", "").endswith(
            "require_permission.<locals>.checker"
        ):
            for cell in call.__closure__ or ():
                val = cell.cell_contents
                if isinstance(val, frozenset):
                    found.append(val)
        for sub in getattr(dep, "dependencies", []) or []:
            walk(sub)

    walk(route.dependant)
    return found


def _user_with(perms: set[str]):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        effective_permissions=frozenset(perms),
        roles=[],
    )


async def _call_checker(route, user):
    """Invoke the route's permission checker as FastAPI would."""
    checker = None
    for route_dep in [route.dependant, *_iter_deps(route.dependant)]:
        call = getattr(route_dep, "call", None)
        if call is not None and getattr(call, "__qualname__", "").endswith(
            "require_permission.<locals>.checker"
        ):
            checker = call
            break
    assert checker is not None, "no require_permission checker on route"
    fake_request = SimpleNamespace(
        client=None, headers={}, method="POST", url=SimpleNamespace(path="/test")
    )
    return await checker(request=fake_request, user=user)


def _iter_deps(dep):
    for sub in getattr(dep, "dependencies", []) or []:
        yield sub
        yield from _iter_deps(sub)


# --- which permission each splittable route must gate on ----------------------

CASES = [
    ("/api/invoices/{invoice_id}/approve", "POST", PERM_INVOICE_APPROVE),
    # Reject is the other half of the approve duty: an approver must be able to
    # reject, and a custom role from which `invoice.approve` was stripped must
    # not be able to reject through the back door. Same permission as approve.
    ("/api/invoices/{invoice_id}/reject", "POST", PERM_INVOICE_APPROVE),
    ("/api/payments", "POST", PERM_PAYMENT_EXECUTE),
    ("/api/payments/runs", "POST", PERM_PAYMENT_RUN_APPROVE),
]


@pytest.mark.parametrize("path,method,perm", CASES)
def test_route_gates_on_expected_permission(path, method, perm):
    route = _find_route(path, method)
    captured = _permission_checkers(route)
    assert captured, f"{method} {path} is not permission-gated (still on require_roles?)"
    assert any(perm in s for s in captured), (
        f"{method} {path} must gate on {perm}; captured {captured}"
    )


@pytest.mark.parametrize("path,method,perm", CASES)
@pytest.mark.asyncio
async def test_holder_allowed_nonholder_denied(path, method, perm):
    route = _find_route(path, method)

    # A user holding the required permission passes.
    allowed = await _call_checker(route, _user_with({perm}))
    assert allowed is not None

    # A user with no permissions (e.g. a custom role with the duty stripped) is
    # rejected with 403 — the back door is closed.
    with pytest.raises(HTTPException) as exc:
        await _call_checker(route, _user_with(set()))
    assert exc.value.status_code == 403
