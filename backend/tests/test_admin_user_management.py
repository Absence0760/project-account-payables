"""Unit tests for the admin user-management privilege-escalation guards
(`app/api/admin.py::_authorize_role_grant` / `_validate_admin_set_password`).

Regression coverage for issue #158: a `user.manage`-only actor must not be able
to (a) grant a role carrying more authority than they hold — most importantly
the system `admin` role — or (b) reset an account's password to a trivial value
that skips the complexity policy. Pure, no DB — the helpers take lightweight
stand-ins for the caller (`.roles` / `.effective_permissions`) and Role rows.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import _authorize_role_grant, _validate_admin_set_password
from app.api.deps import ROLE_ADMIN, ROLE_AP_CLERK
from app.api.permissions import (
    ALL_PERMISSIONS,
    PERM_PAYMENT_EXECUTE,
    PERM_USER_MANAGE,
    ROLE_DEFAULT_PERMISSIONS,
    effective_permissions,
)


def _sys_role(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, organization_id=None, permissions=None)


def _custom_role(perms: list[str], name: str = "Custom") -> SimpleNamespace:
    return SimpleNamespace(name=name, organization_id=uuid.uuid4(), permissions=perms)


def _caller(roles: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(roles=roles, effective_permissions=effective_permissions(roles))


# --------------------------------------------------------------------------
# _authorize_role_grant
# --------------------------------------------------------------------------


def test_admin_caller_may_grant_admin():
    admin = _caller([_sys_role(ROLE_ADMIN)])
    _authorize_role_grant(admin, [_sys_role(ROLE_ADMIN)])  # no raise


def test_user_manage_only_caller_cannot_grant_admin():
    """The core exploit: a custom 'User Admin' role carrying ONLY user.manage
    tries to grant the system admin role."""
    caller = _caller([_custom_role([PERM_USER_MANAGE], name="UserAdmin")])
    with pytest.raises(HTTPException) as exc:
        _authorize_role_grant(caller, [_sys_role(ROLE_ADMIN)])
    assert exc.value.status_code == 403


def test_full_catalog_non_admin_still_cannot_grant_admin():
    """Even a caller holding every *catalog* permission can't grant the admin
    system role — admin carries non-catalog superuser authority the subset
    check can't see."""
    caller = _caller([_custom_role(list(ALL_PERMISSIONS), name="Superuser")])
    assert caller.effective_permissions == frozenset(ALL_PERMISSIONS)
    with pytest.raises(HTTPException) as exc:
        _authorize_role_grant(caller, [_sys_role(ROLE_ADMIN)])
    assert exc.value.status_code == 403


def test_cannot_grant_permission_not_held():
    caller = _caller([_custom_role([PERM_USER_MANAGE], name="UserAdmin")])
    target = _custom_role([PERM_PAYMENT_EXECUTE], name="Payer")
    with pytest.raises(HTTPException) as exc:
        _authorize_role_grant(caller, [target])
    assert exc.value.status_code == 403


def test_can_grant_subset_role():
    """A caller may hand out a role whose catalog permissions are a subset of
    their own (and which isn't the admin system role)."""
    caller = _caller([_custom_role([PERM_USER_MANAGE], name="UserAdmin")])
    # ap_clerk confers no catalog permission — the empty set is a subset of any.
    _authorize_role_grant(caller, [_sys_role(ROLE_AP_CLERK)])
    # A custom role granting the same permission the caller holds is fine too.
    _authorize_role_grant(caller, [_custom_role([PERM_USER_MANAGE])])


def test_admin_caller_may_grant_any_role():
    admin = _caller([_sys_role(ROLE_ADMIN)])
    assert admin.effective_permissions == ROLE_DEFAULT_PERMISSIONS[ROLE_ADMIN]
    _authorize_role_grant(admin, [_custom_role([PERM_PAYMENT_EXECUTE])])  # no raise


def test_grant_guard_falls_back_when_effective_permissions_missing():
    """The guard recomputes from .roles if the caller has no cached
    effective_permissions attribute (defensive)."""
    caller = SimpleNamespace(roles=[_custom_role([PERM_USER_MANAGE], name="UserAdmin")])
    with pytest.raises(HTTPException):
        _authorize_role_grant(caller, [_sys_role(ROLE_ADMIN)])


# --------------------------------------------------------------------------
# _validate_admin_set_password
# --------------------------------------------------------------------------


@pytest.mark.parametrize("weak", ["short", "abc123", "alllowercase123", "NOLOWER123", "NoDigits"])
def test_admin_set_password_rejects_weak(weak: str):
    with pytest.raises(HTTPException) as exc:
        _validate_admin_set_password(weak)
    assert exc.value.status_code == 422


def test_admin_set_password_accepts_complex():
    _validate_admin_set_password("Str0ngEnoughPass")  # ≥12, upper+lower+digit


# --------------------------------------------------------------------------
# Issue #160 — admin password reset must revoke the target's active sessions,
# matching the existing role-change/deactivation forced-logout guarantee.
# Exercised end-to-end against the real `/api/admin/users/{id}` endpoint
# (realdb), not just the pure helpers above.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_password_reset_revokes_target_sessions(realdb, monkeypatch):
    """A `body.password` PATCH must call revoke_user_sessions for the target —
    without it, a token an attacker already holds survives the reset, which
    defeats the whole point of resetting credentials believed compromised."""
    calls: list = []

    async def _fake_revoke(user_id):
        calls.append(user_id)
        return []

    monkeypatch.setattr("app.api.admin.revoke_user_sessions", _fake_revoke)

    target_id = realdb.info("a").users["ap_clerk"]
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.patch(
            f"/api/admin/users/{target_id}",
            json={"password": "Str0ngEnoughPass"},
        )
    assert resp.status_code == 200
    assert calls == [target_id]


@pytest.mark.asyncio
async def test_admin_update_user_other_field_change_does_not_revoke(realdb, monkeypatch):
    """A plain field edit (no role change, no deactivation, no password reset)
    must not force-logout the target — only the sensitive branches should."""
    calls: list = []

    async def _fake_revoke(user_id):
        calls.append(user_id)
        return []

    monkeypatch.setattr("app.api.admin.revoke_user_sessions", _fake_revoke)

    target_id = realdb.info("a").users["ap_clerk"]
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.patch(
            f"/api/admin/users/{target_id}",
            json={"full_name": "Renamed Clerk"},
        )
    assert resp.status_code == 200
    assert calls == []
