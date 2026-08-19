"""`GET /api/invoices/assignable-reviewers` — the approver picker's own source.

The picker used to read `GET /api/admin/users`, which is
`require_roles(ROLE_ADMIN)`. For an ap_manager — a role
`POST /api/invoices/{id}/assign` itself accepts — that call 403'd, the list
stayed empty, and an invoice on a workflow whose approval step is
`approver_strategy: "manual"` (the seeded default) could not be submitted at
all.

The fix is a NARROWER endpoint, not a wider `/admin/users`: the response
carries only id / full_name / is_active. Email, roles and audit metadata are
exactly what makes the admin directory admin-only, and none of it is needed to
pick an approver. Pinned here so nobody "simplifies" this back onto
`AdminUserResponse`.

Requires the dev Postgres (`pnpm db:up`).
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.models.user import Role, User, UserRole

pytestmark = pytest.mark.asyncio

TENANT = "a"
PATH = "/api/invoices/assignable-reviewers"


async def _add_user(realdb, *, full_name: str, role_name: str, is_active: bool = True) -> uuid.UUID:
    """Seed one control-plane user in tenant A holding `role_name`."""
    from app.utils.passwords import pwd_context

    info = realdb.info(TENANT)
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        role = (
            (
                await s.execute(
                    select(Role).where(
                        Role.name == role_name,
                        (Role.organization_id.is_(None)) | (Role.organization_id == info.org_id),
                    )
                )
            )
            .scalars()
            .first()
        )
        assert role is not None, role_name
        uid = uuid.uuid4()
        s.add(
            User(
                id=uid,
                email=f"{uuid.uuid4().hex[:10]}@assignable.test",
                full_name=full_name,
                hashed_password=pwd_context.hash("Passw0rd!xyz"),
                is_active=is_active,
                organization_id=info.org_id,
                must_change_password=False,
            )
        )
        await s.flush()
        s.add(UserRole(user_id=uid, role_id=role.id))
        await s.commit()
    return uid


async def _add_custom_role(realdb, *, name: str, permissions: list[str]) -> uuid.UUID:
    info = realdb.info(TENANT)
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        role = Role(
            id=uuid.uuid4(),
            name=name,
            description="assignable-reviewers test role",
            organization_id=info.org_id,
            permissions=permissions,
        )
        s.add(role)
        await s.commit()
        return role.id


async def _assign_role(realdb, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        s.add(UserRole(user_id=user_id, role_id=role_id))
        await s.commit()


async def test_ap_manager_gets_a_usable_list(realdb):
    """The whole point: the role that may assign can read the candidates."""
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(PATH)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    names = {u["full_name"] for u in body}
    # The harness seeds one user per system role, named for the role.
    assert {"admin", "ap_manager", "cfo"} <= names
    assert all(u["is_active"] is True for u in body)
    assert all(uuid.UUID(u["id"]) for u in body)


async def test_response_carries_no_email_or_other_pii(realdb):
    """The narrower shape IS the fix — never widen this to the admin
    directory's projection."""
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(PATH)
    assert resp.status_code == 200
    body = resp.json()
    assert body, "expected at least the seeded role users"
    for entry in body:
        assert set(entry) == {"id", "full_name", "is_active"}
    payload = json.dumps(body)
    assert "@" not in payload
    for leaked in ("email", "roles", "last_login", "created_at", "permissions"):
        assert leaked not in payload


async def test_a_clerk_is_not_offered_as_a_reviewer(realdb):
    """ap_clerk holds no `invoice.approve`, so assigning to one is a dead
    end — the picker must never offer it."""
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(PATH)
    assert resp.status_code == 200
    assert "ap_clerk" not in {u["full_name"] for u in resp.json()}


async def test_inactive_users_are_excluded(realdb):
    await _add_user(realdb, full_name="Retired Manager", role_name="ap_manager", is_active=False)

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(PATH)
    assert resp.status_code == 200
    assert "Retired Manager" not in {u["full_name"] for u in resp.json()}


async def test_a_custom_role_granting_invoice_approve_is_offered(realdb):
    """Candidacy is resolved through `effective_permissions`, not a hardcoded
    system-role list — so an org that splits duties onto a custom role still
    gets a working picker."""
    from app.api.permissions import PERM_INVOICE_APPROVE

    role_id = await _add_custom_role(
        realdb, name=f"approver-{uuid.uuid4().hex[:6]}", permissions=[PERM_INVOICE_APPROVE]
    )
    uid = await _add_user(realdb, full_name="Custom Approver", role_name="ap_clerk")
    await _assign_role(realdb, uid, role_id)

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(PATH)
    assert resp.status_code == 200
    assert "Custom Approver" in {u["full_name"] for u in resp.json()}


async def test_a_custom_role_without_invoice_approve_is_not_offered(realdb):
    from app.api.permissions import PERM_VENDOR_MANAGE

    role_id = await _add_custom_role(
        realdb, name=f"vendors-only-{uuid.uuid4().hex[:6]}", permissions=[PERM_VENDOR_MANAGE]
    )
    uid = await _add_user(realdb, full_name="Vendor Steward", role_name="ap_clerk")
    await _assign_role(realdb, uid, role_id)

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(PATH)
    assert resp.status_code == 200
    assert "Vendor Steward" not in {u["full_name"] for u in resp.json()}


async def test_rbac_matches_the_assign_endpoint(realdb):
    """Reading the candidate list and acting on it are the same privilege:
    `POST /api/invoices/{id}/assign` is `require_roles(ADMIN, AP_MANAGER)`."""
    for role in ("admin", "ap_manager"):
        async with realdb.client(key=TENANT, role=role) as c:
            assert (await c.get(PATH)).status_code == 200, role

    for role in ("ap_clerk", "cfo"):
        async with realdb.client(key=TENANT, role=role) as c:
            assert (await c.get(PATH)).status_code == 403, role


async def test_requires_auth(realdb):
    async with realdb.client(key=TENANT, role=None) as c:
        resp = await c.get(PATH)
    assert resp.status_code == 401


async def test_scoped_to_the_callers_org(realdb):
    """Tenant isolation: another org's approvers are never candidates."""
    other = realdb.info("b")
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(PATH)
    assert resp.status_code == 200
    ids = {u["id"] for u in resp.json()}
    assert not ids & {str(uid) for uid in other.users.values()}


async def test_literal_path_is_not_swallowed_by_the_invoice_id_route(realdb):
    """Route ordering guard: `/{invoice_id}` would parse `assignable-reviewers`
    as a UUID and 422 before the handler ever ran."""
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(PATH)
    assert resp.status_code != 422
