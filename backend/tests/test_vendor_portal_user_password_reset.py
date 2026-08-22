"""AP-admin-triggered supplier-portal user password reset.

Before `POST /api/vendors/{vendor_id}/portal-users/{vendor_user_id}/reset-password`
existed, the only recovery for a locked-out vendor portal user was AP deleting
and re-inviting them — a new `VendorUser.id` that breaks anything referencing
the old one (chat authorship, audit rows, notification-preferences), and a new
temp password/new account rather than a reset on the same one.

Exercised through the real HTTP surface + real Postgres via the `realdb`
harness so the round-trip (mint a new temp password → hash it onto the SAME
row → force `must_change_password` → the OLD password stops authenticating →
the audit row lands) is proven end to end, not just at the unit level.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.vendor_user import VendorUser
from app.models.workflow import AuditLog
from tests.test_session_management import FakeRedis

TENANT = "a"


@pytest.fixture
def mk(realdb):
    return realdb.sessionmaker(TENANT)


@pytest.fixture
def fake_redis(monkeypatch):
    """A real portal login goes through `session_management.register_session`,
    which needs the richer zset+hash Redis stand-in (`FakeRedis`), not the
    key/value-only stub the autouse `_autouse_fake_redis` fixture installs —
    that one has no `zadd`. A test-requested fixture overrides the autouse one
    (fixtures requested by name run after autouse ones)."""
    fake = FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.redis.get_redis", _get_redis)
    return fake


async def _create_vendor(client) -> str:
    resp = await client.post(
        "/api/vendors",
        json={"name": f"Reset Test Vendor {uuid.uuid4().hex[:8]}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _invite_portal_user(client, vendor_id: str, email: str) -> dict:
    resp = await client.post(
        f"/api/vendors/{vendor_id}/portal-users",
        json={"email": email, "full_name": "Locked Out Vendor"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_reset_mints_new_temp_password_and_forces_change(realdb, mk):
    """Happy path: the response carries a fresh temp password distinct from
    the invite one, the SAME VendorUser id, and `must_change_password: true`
    so the vendor is forced through the change-password flow on next login —
    mirroring `invite_vendor_portal_user`."""
    email = f"locked-{uuid.uuid4().hex[:8]}@vendor.test"
    async with realdb.client(key=TENANT, role="admin") as client:
        vendor_id = await _create_vendor(client)
        invited = await _invite_portal_user(client, vendor_id, email)
        vendor_user_id = invited["user"]["id"]
        old_temp_password = invited["temp_password"]

        resp = await client.post(
            f"/api/vendors/{vendor_id}/portal-users/{vendor_user_id}/reset-password"
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["user"]["id"] == vendor_user_id
    assert body["user"]["must_change_password"] is True
    new_temp_password = body["temp_password"]
    assert new_temp_password
    assert new_temp_password != old_temp_password

    async with mk() as s:
        vu = (
            await s.execute(select(VendorUser).where(VendorUser.id == uuid.UUID(vendor_user_id)))
        ).scalar_one()
        assert vu.must_change_password is True
        # A real hash — not the temp password stored/echoed as plaintext.
        assert vu.hashed_password != new_temp_password
        assert vu.hashed_password


@pytest.mark.asyncio
async def test_old_password_stops_working_after_reset(realdb, mk, fake_redis):
    """The whole point: once reset, the OLD temp password no longer
    authenticates at the real portal login endpoint, and the NEW one does."""
    email = f"locked-{uuid.uuid4().hex[:8]}@vendor.test"
    async with realdb.client(key=TENANT, role="admin") as admin_client:
        vendor_id = await _create_vendor(admin_client)
        invited = await _invite_portal_user(admin_client, vendor_id, email)
        vendor_user_id = invited["user"]["id"]
        old_temp_password = invited["temp_password"]

        # Sanity: the pre-reset password actually works.
        async with realdb.client(key=TENANT, role=None) as anon_client:
            pre_resp = await anon_client.post(
                "/api/portal/auth/login",
                json={"email": email, "password": old_temp_password},
            )
        assert pre_resp.status_code == 200, pre_resp.text

        reset_resp = await admin_client.post(
            f"/api/vendors/{vendor_id}/portal-users/{vendor_user_id}/reset-password"
        )
    assert reset_resp.status_code == 200, reset_resp.text
    new_temp_password = reset_resp.json()["temp_password"]

    async with realdb.client(key=TENANT, role=None) as anon_client:
        old_login = await anon_client.post(
            "/api/portal/auth/login",
            json={"email": email, "password": old_temp_password},
        )
        assert old_login.status_code == 401

        new_login = await anon_client.post(
            "/api/portal/auth/login",
            json={"email": email, "password": new_temp_password},
        )
    assert new_login.status_code == 200, new_login.text


@pytest.mark.asyncio
async def test_reset_writes_an_audit_row(realdb, mk):
    """Status/credential changes on a supplier-portal identity are audited
    like every other vendor mutation (project invariant — append-only audit
    trail on state changes)."""
    email = f"locked-{uuid.uuid4().hex[:8]}@vendor.test"
    async with realdb.client(key=TENANT, role="admin") as client:
        vendor_id = await _create_vendor(client)
        invited = await _invite_portal_user(client, vendor_id, email)
        vendor_user_id = invited["user"]["id"]

        resp = await client.post(
            f"/api/vendors/{vendor_id}/portal-users/{vendor_user_id}/reset-password"
        )
    assert resp.status_code == 200, resp.text

    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "vendor_user",
                        AuditLog.entity_id == uuid.UUID(vendor_user_id),
                        AuditLog.action == "vendor_portal_user.password_reset",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    details = rows[0].details or {}
    assert details.get("vendor_id") == vendor_id
    # PII-lean: never the temp password or the vendor user's email.
    assert "temp_password" not in details
    assert email not in str(details)


@pytest.mark.asyncio
async def test_reset_unknown_vendor_user_is_404(realdb):
    async with realdb.client(key=TENANT, role="admin") as client:
        vendor_id = await _create_vendor(client)
        resp = await client.post(
            f"/api/vendors/{vendor_id}/portal-users/{uuid.uuid4()}/reset-password"
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reset_requires_admin_or_ap_manager_role(realdb):
    """Same permission gate as invite/delete — an ap_clerk cannot reset a
    supplier's credential."""
    email = f"locked-{uuid.uuid4().hex[:8]}@vendor.test"
    async with realdb.client(key=TENANT, role="admin") as admin_client:
        vendor_id = await _create_vendor(admin_client)
        invited = await _invite_portal_user(admin_client, vendor_id, email)
    vendor_user_id = invited["user"]["id"]

    async with realdb.client(key=TENANT, role="ap_clerk") as clerk_client:
        resp = await clerk_client.post(
            f"/api/vendors/{vendor_id}/portal-users/{vendor_user_id}/reset-password"
        )
    assert resp.status_code == 403
