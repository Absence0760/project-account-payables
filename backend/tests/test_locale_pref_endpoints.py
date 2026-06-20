"""Set-locale endpoints — employee (PATCH /api/auth/me) + supplier portal
(PATCH /api/portal/auth/me).

DB-backed (`realdb`): the behaviour under test is persistence + validation of
the account-level email-language preference, and that an unknown locale is
rejected (so the stored value is always a known locale). The user / vendor sets
their OWN locale (RBAC = the authenticated principal).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.api.deps import create_vendor_access_token
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser

pytestmark = pytest.mark.asyncio

TENANT = "a"


async def _reset_admin_locale(realdb) -> uuid.UUID:
    """Clear the shared admin user's locale before a test.

    The realdb harness truncates only TENANT data between tests; control-plane
    `users` rows persist, so these tests reset the field they mutate to avoid
    cross-test contamination (the admin user is shared by every realdb test).
    """
    admin_id = realdb.info(TENANT).users["admin"]
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        user = (await s.execute(select(User).where(User.id == admin_id))).scalar_one()
        user.locale = None
        await s.commit()
    return admin_id


# --------------------------------------------------------------------------
# Employee: PATCH /api/auth/me { locale }
# --------------------------------------------------------------------------


async def test_employee_set_locale_persists(realdb):
    await _reset_admin_locale(realdb)
    async with realdb.client(key=TENANT, role="admin") as client:
        # Default: no locale set yet.
        me = (await client.get("/api/auth/me")).json()
        assert me["locale"] is None

        resp = await client.patch("/api/auth/me", json={"locale": "de"})
        assert resp.status_code == 200
        assert resp.json()["locale"] == "de"

    # Persisted to the control-plane row.
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        admin_id = realdb.info(TENANT).users["admin"]
        user = (await s.execute(select(User).where(User.id == admin_id))).scalar_one()
        assert user.locale == "de"


async def test_employee_reject_unknown_locale(realdb):
    await _reset_admin_locale(realdb)
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.patch("/api/auth/me", json={"locale": "zz-ZZ"})
        assert resp.status_code == 422

    # Unchanged in the DB.
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        admin_id = realdb.info(TENANT).users["admin"]
        user = (await s.execute(select(User).where(User.id == admin_id))).scalar_one()
        assert user.locale != "zz-ZZ"


async def test_employee_clear_locale_with_empty_string(realdb):
    async with realdb.client(key=TENANT, role="admin") as client:
        await client.patch("/api/auth/me", json={"locale": "fr"})
        cleared = await client.patch("/api/auth/me", json={"locale": ""})
        assert cleared.status_code == 200
        assert cleared.json()["locale"] is None


async def test_employee_omitting_locale_leaves_it_untouched(realdb):
    async with realdb.client(key=TENANT, role="admin") as client:
        await client.patch("/api/auth/me", json={"locale": "es"})
        # A profile update that doesn't mention locale must not wipe it.
        resp = await client.patch("/api/auth/me", json={"full_name": "Renamed Admin"})
        assert resp.status_code == 200
        assert resp.json()["locale"] == "es"
        assert resp.json()["full_name"] == "Renamed Admin"


async def test_employee_set_locale_requires_auth(realdb):
    async with realdb.client(key=TENANT, role=None) as client:
        resp = await client.patch("/api/auth/me", json={"locale": "de"})
        assert resp.status_code in (401, 403)


# --------------------------------------------------------------------------
# Supplier portal: PATCH /api/portal/auth/me { locale }
# --------------------------------------------------------------------------


async def _seed_vendor_user(mk, org_id) -> tuple[uuid.UUID, uuid.UUID]:
    vendor_id = uuid.uuid4()
    vu_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=vendor_id,
                name="Acme Supply",
                organization_id=org_id,
                status="active",
                source="manual",
            )
        )
        s.add(
            VendorUser(
                id=vu_id,
                vendor_id=vendor_id,
                email=f"{vu_id}@portal.test",
                full_name="Portal User",
                hashed_password="x",
                is_active=True,
            )
        )
        await s.commit()
    return vendor_id, vu_id


def _portal_client(realdb, vu_id: uuid.UUID, vendor_id: uuid.UUID):
    token = create_vendor_access_token(vu_id, vendor_id)
    client = realdb.client(key=TENANT, role=None)
    client.headers["Authorization"] = f"Bearer {token}"
    return client


async def test_portal_set_locale_persists(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed_vendor_user(mk, org_id)

    async with _portal_client(realdb, vu_id, vendor_id) as client:
        me = (await client.get("/api/portal/auth/me")).json()
        assert me["locale"] is None

        resp = await client.patch("/api/portal/auth/me", json={"locale": "pt-BR"})
        assert resp.status_code == 200
        assert resp.json()["locale"] == "pt-BR"

    async with mk() as s:
        vu = (await s.execute(select(VendorUser).where(VendorUser.id == vu_id))).scalar_one()
        assert vu.locale == "pt-BR"


async def test_portal_reject_unknown_locale(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, vu_id = await _seed_vendor_user(mk, org_id)

    async with _portal_client(realdb, vu_id, vendor_id) as client:
        resp = await client.patch("/api/portal/auth/me", json={"locale": "klingon"})
        assert resp.status_code == 422

    async with mk() as s:
        vu = (await s.execute(select(VendorUser).where(VendorUser.id == vu_id))).scalar_one()
        assert vu.locale is None


async def test_portal_set_locale_requires_vendor_auth(realdb):
    # No token at all → rejected.
    async with realdb.client(key=TENANT, role=None) as client:
        resp = await client.patch("/api/portal/auth/me", json={"locale": "de"})
        assert resp.status_code in (401, 403)
