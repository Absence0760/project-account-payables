"""Unit tests for the granular-permission layer (`app/api/permissions.py`).

Covers:
* the system-role default map reproduces today's RBAC matrix exactly,
* the effective-permissions union (system default map ∪ custom stored list),
* `sanitize_permissions` drops unknown / malformed entries,
* `require_permission` enforces any-of semantics + rejects typos at import time.

Pure, no DB — the resolver takes scalars / lightweight stand-ins.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    require_permission,
)
from app.api.permissions import (
    ALL_PERMISSIONS,
    PERM_INVOICE_APPROVE,
    PERM_PAYMENT_EXECUTE,
    PERM_PAYMENT_RUN_APPROVE,
    PERM_PAYMENT_VOID,
    PERM_USER_MANAGE,
    PERM_VENDOR_BANK_CHANGE_APPROVE,
    PERM_VENDOR_BLOCK,
    PERM_VENDOR_MANAGE,
    ROLE_DEFAULT_PERMISSIONS,
    effective_permissions,
    permissions_for_role,
    sanitize_permissions,
)


def _system_role(name: str):
    return SimpleNamespace(name=name, organization_id=None, permissions=None)


def _custom_role(permissions):
    return SimpleNamespace(name="Custom", organization_id=uuid.uuid4(), permissions=permissions)


# ---------- system-role default map = today's matrix --------------------------


def test_admin_has_every_permission():
    """admin keeps the whole catalog — unchanged from `require_roles(ADMIN)`."""
    assert ROLE_DEFAULT_PERMISSIONS[ROLE_ADMIN] == frozenset(ALL_PERMISSIONS)


def test_ap_manager_default_matches_prior_role_matrix():
    """ap_manager held: invoice approve, run create+execute, vendor bank-change
    approve, vendor block, vendor manage — but NOT payment void (admin/cfo only)."""
    assert ROLE_DEFAULT_PERMISSIONS[ROLE_AP_MANAGER] == frozenset(
        {
            PERM_INVOICE_APPROVE,
            PERM_PAYMENT_RUN_APPROVE,
            PERM_PAYMENT_EXECUTE,
            PERM_VENDOR_BANK_CHANGE_APPROVE,
            PERM_VENDOR_BLOCK,
            PERM_VENDOR_MANAGE,
        }
    )
    assert PERM_PAYMENT_VOID not in ROLE_DEFAULT_PERMISSIONS[ROLE_AP_MANAGER]
    assert PERM_USER_MANAGE not in ROLE_DEFAULT_PERMISSIONS[ROLE_AP_MANAGER]


def test_cfo_default_matches_prior_role_matrix():
    """cfo held: invoice approve, run create+execute, payment void — but not
    vendor master-data / user management."""
    assert ROLE_DEFAULT_PERMISSIONS[ROLE_CFO] == frozenset(
        {
            PERM_INVOICE_APPROVE,
            PERM_PAYMENT_RUN_APPROVE,
            PERM_PAYMENT_EXECUTE,
            PERM_PAYMENT_VOID,
        }
    )
    assert PERM_VENDOR_BANK_CHANGE_APPROVE not in ROLE_DEFAULT_PERMISSIONS[ROLE_CFO]
    assert PERM_USER_MANAGE not in ROLE_DEFAULT_PERMISSIONS[ROLE_CFO]


def test_ap_clerk_default_is_empty():
    """A clerk holds none of the sensitive permissions — unchanged."""
    assert ROLE_DEFAULT_PERMISSIONS[ROLE_AP_CLERK] == frozenset()


def test_user_manage_default_is_admin_only():
    holders = {r for r, perms in ROLE_DEFAULT_PERMISSIONS.items() if PERM_USER_MANAGE in perms}
    assert holders == {ROLE_ADMIN}


def test_payment_void_default_is_admin_and_cfo_only():
    holders = {r for r, perms in ROLE_DEFAULT_PERMISSIONS.items() if PERM_PAYMENT_VOID in perms}
    assert holders == {ROLE_ADMIN, ROLE_CFO}


# ---------- permissions_for_role + effective_permissions union ----------------


def test_system_role_ignores_stored_permissions_column():
    """A system row's `permissions` column is NULL and irrelevant — even if it
    somehow carried a value, the static map wins (can't widen a system role)."""
    rogue = SimpleNamespace(
        name=ROLE_AP_CLERK, organization_id=None, permissions=[PERM_PAYMENT_EXECUTE]
    )
    assert (
        permissions_for_role(
            name=rogue.name, organization_id=rogue.organization_id, permissions=rogue.permissions
        )
        == frozenset()
    )


def test_custom_role_resolves_from_stored_list():
    role = _custom_role([PERM_INVOICE_APPROVE])
    assert permissions_for_role(
        name=role.name, organization_id=role.organization_id, permissions=role.permissions
    ) == frozenset({PERM_INVOICE_APPROVE})


def test_custom_role_with_none_or_empty_grants_nothing():
    assert (
        permissions_for_role(name="C", organization_id=uuid.uuid4(), permissions=None)
        == frozenset()
    )
    assert (
        permissions_for_role(name="C", organization_id=uuid.uuid4(), permissions=[]) == frozenset()
    )


def test_effective_permissions_unions_across_roles():
    """A clerk (no perms) + a custom role granting only invoice.approve → the
    union is exactly invoice.approve. This is the SoD split in action."""
    roles = [_system_role(ROLE_AP_CLERK), _custom_role([PERM_INVOICE_APPROVE])]
    assert effective_permissions(roles) == frozenset({PERM_INVOICE_APPROVE})


def test_effective_permissions_system_plus_custom():
    roles = [_system_role(ROLE_CFO), _custom_role([PERM_VENDOR_MANAGE])]
    out = effective_permissions(roles)
    assert PERM_PAYMENT_VOID in out  # from cfo
    assert PERM_VENDOR_MANAGE in out  # from custom
    assert PERM_USER_MANAGE not in out  # neither grants it


def test_effective_permissions_empty_for_no_roles():
    assert effective_permissions([]) == frozenset()
    assert effective_permissions(None) == frozenset()


# ---------- sanitize_permissions ---------------------------------------------


def test_sanitize_drops_unknown_and_dedupes_in_catalog_order():
    raw = [PERM_PAYMENT_EXECUTE, "bogus", PERM_INVOICE_APPROVE, PERM_INVOICE_APPROVE]
    out = sanitize_permissions(raw)
    # de-duped, only-known, and ordered by catalog order (approve before execute)
    assert out == [PERM_INVOICE_APPROVE, PERM_PAYMENT_EXECUTE]


def test_sanitize_handles_non_list_inputs():
    assert sanitize_permissions(None) == []
    assert sanitize_permissions("invoice.approve") == []
    assert sanitize_permissions(42) == []


# ---------- require_permission dependency -------------------------------------


def _fake_user_with_perms(*perms: str) -> MagicMock:
    user = MagicMock(spec=["id", "organization_id", "effective_permissions"])
    user.id = uuid.uuid4()
    user.organization_id = uuid.uuid4()
    user.effective_permissions = frozenset(perms)
    return user


def _fake_request() -> MagicMock:
    req = MagicMock()
    req.method = "POST"
    req.url.path = "/api/payments/runs/x/execute"
    return req


@pytest.mark.asyncio
async def test_require_permission_accepts_holder():
    checker = require_permission(PERM_PAYMENT_EXECUTE)
    user = _fake_user_with_perms(PERM_PAYMENT_EXECUTE)
    assert (await checker(request=_fake_request(), user=user)) is user


@pytest.mark.asyncio
async def test_require_permission_any_of_semantics():
    checker = require_permission(PERM_PAYMENT_EXECUTE, PERM_PAYMENT_VOID)
    user = _fake_user_with_perms(PERM_PAYMENT_VOID)
    assert (await checker(request=_fake_request(), user=user)) is user


@pytest.mark.asyncio
async def test_require_permission_rejects_non_holder():
    """A custom role granted only invoice.approve is refused payment execution —
    the e2e SoD guarantee, asserted at the dependency level."""
    checker = require_permission(PERM_PAYMENT_EXECUTE)
    user = _fake_user_with_perms(PERM_INVOICE_APPROVE)
    with pytest.raises(HTTPException) as exc:
        await checker(request=_fake_request(), user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_permission_rejects_no_permissions():
    checker = require_permission(PERM_PAYMENT_EXECUTE)
    user = _fake_user_with_perms()
    with pytest.raises(HTTPException) as exc:
        await checker(request=_fake_request(), user=user)
    assert exc.value.status_code == 403


def test_require_permission_rejects_empty_at_import_time():
    with pytest.raises(ValueError):
        require_permission()


def test_require_permission_rejects_typo_at_import_time():
    with pytest.raises(ValueError):
        require_permission("payment.exceute")


@pytest.mark.asyncio
async def test_require_permission_logs_denials(caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="app.api.deps")
    checker = require_permission(PERM_PAYMENT_EXECUTE)
    user = _fake_user_with_perms(PERM_INVOICE_APPROVE)
    with pytest.raises(HTTPException):
        await checker(request=_fake_request(), user=user)
    assert any("RBAC denied (permission)" in rec.message for rec in caplog.records)


def test_every_label_covers_catalog():
    """No permission ships without a human label for the role editor."""
    from app.api.permissions import PERMISSION_LABELS

    assert set(PERMISSION_LABELS) == set(ALL_PERMISSIONS)


# ---------- POST /runs/{id}/approve — migrated off require_roles(ROLE_CFO) ---


@pytest.mark.asyncio
async def test_approve_payment_run_route_wires_run_approve_permission():
    """`POST /runs/{id}/approve` (the CFO sign-off endpoint) used to gate on
    `require_roles(ROLE_CFO)` — a hardcoded role name, inconsistent with its
    sibling `POST /runs` (create/approve), which already gates on
    `payment_run.approve`. It now shares that permission, so:

    * a custom role holding ONLY `payment_run.approve` — no CFO role, nothing
      else granted — can call it (the whole point of the migration: an org
      can split this sign-off duty away from the CFO title), and
    * the old assumption still holds — a CFO, who carries this permission by
      default per `ROLE_DEFAULT_PERMISSIONS[ROLE_CFO]`, can still call it.

    Extracts the actual `Depends(...)` callable off the route function and
    drives it directly (same technique as the `require_permission` tests
    above), so a regression back to role-only gating fails here rather than
    only surfacing in an e2e spec.
    """
    import inspect

    from app.api.payments import approve_payment_run

    checker = inspect.signature(approve_payment_run).parameters["user"].default.dependency

    custom_role_user = _fake_user_with_perms(PERM_PAYMENT_RUN_APPROVE)
    assert (await checker(request=_fake_request(), user=custom_role_user)) is custom_role_user

    cfo_like_user = _fake_user_with_perms(*ROLE_DEFAULT_PERMISSIONS[ROLE_CFO])
    assert (await checker(request=_fake_request(), user=cfo_like_user)) is cfo_like_user

    # A holder of an unrelated permission alone (e.g. vendor management, no
    # run-approval) is refused — the permission check is still enforced, not
    # bypassed by the migration.
    other_user = _fake_user_with_perms(PERM_VENDOR_MANAGE)
    with pytest.raises(HTTPException) as exc:
        await checker(request=_fake_request(), user=other_user)
    assert exc.value.status_code == 403
