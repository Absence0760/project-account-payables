"""Real-DB coverage for the notifications router.

Covers list (scoped to current user + cross-user isolation), unread-count,
mark-read (404 for another user's row — no enumeration), read-all, the
paginated envelope shape, and the preferences GET/PATCH round-trip.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.notification import Notification
from app.models.user import User


async def _add_notification(mk, org_id, recipient_id, *, read=False, event="invoice_approved"):
    async with mk() as s:
        n = Notification(
            organization_id=org_id,
            correlation_id=uuid.uuid4(),
            recipient_user_id=recipient_id,
            event_type=event,
            entity_type="invoice",
            entity_id=uuid.uuid4(),
            title=f"{event} title",
            body="body",
            read_at=None,
        )
        s.add(n)
        await s.commit()
        await s.refresh(n)
        if read:
            from datetime import UTC, datetime

            n.read_at = datetime.now(UTC)
            await s.commit()
        return n.id


async def test_list_scoped_to_current_user(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    me = realdb.info("a").users["admin"]
    other = realdb.info("a").users["cfo"]

    await _add_notification(mk, org_id, me)
    await _add_notification(mk, org_id, me)
    await _add_notification(mk, org_id, other)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get("/api/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["unread"] == 2
    assert {"items", "total", "unread", "page", "page_size"} <= set(data)
    assert all(item["event_type"] for item in data["items"])


async def test_unread_only_filter_and_count(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    me = realdb.info("a").users["admin"]

    await _add_notification(mk, org_id, me, read=False)
    await _add_notification(mk, org_id, me, read=True)

    async with realdb.client(key="a", role="admin") as c:
        all_resp = (await c.get("/api/notifications")).json()
        unread_resp = (await c.get("/api/notifications?unread_only=true")).json()
        count_resp = (await c.get("/api/notifications/unread-count")).json()

    assert all_resp["total"] == 2
    assert all_resp["unread"] == 1
    assert unread_resp["total"] == 1
    assert count_resp["unread"] == 1


async def test_mark_read_decrements_unread(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    me = realdb.info("a").users["admin"]
    nid = await _add_notification(mk, org_id, me)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/notifications/{nid}/read")
        assert resp.status_code == 200
        assert resp.json()["read_at"] is not None
        # Idempotent: second call still 200.
        resp2 = await c.post(f"/api/notifications/{nid}/read")
        assert resp2.status_code == 200
        count = (await c.get("/api/notifications/unread-count")).json()["unread"]
    assert count == 0


async def test_mark_read_other_users_notification_is_404(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    other = realdb.info("a").users["cfo"]
    nid = await _add_notification(mk, org_id, other)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/notifications/{nid}/read")
    assert resp.status_code == 404
    # The other user's row stays unread (not enumerated, not mutated).
    async with mk() as s:
        n = (await s.execute(select(Notification).where(Notification.id == nid))).scalar_one()
        assert n.read_at is None


async def test_mark_read_missing_is_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/notifications/{uuid.uuid4()}/read")
    assert resp.status_code == 404


async def test_read_all(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    me = realdb.info("a").users["admin"]
    await _add_notification(mk, org_id, me)
    await _add_notification(mk, org_id, me)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/notifications/read-all")
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
        count = (await c.get("/api/notifications/unread-count")).json()["unread"]
    assert count == 0


async def test_cross_tenant_isolation(realdb):
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    me_a = realdb.info("a").users["admin"]
    await _add_notification(mk_a, org_a, me_a)

    # Tenant B's admin sees nothing of A's.
    async with realdb.client(key="b", role="admin") as c:
        data = (await c.get("/api/notifications")).json()
    assert data["total"] == 0


async def test_preferences_round_trip(realdb):
    ctrl_mk = realdb.control_sessionmaker()
    me = realdb.info("a").users["admin"]
    # reset
    async with ctrl_mk() as s:
        u = (await s.execute(select(User).where(User.id == me))).scalar_one()
        u.notification_prefs = {}
        await s.commit()

    async with realdb.client(key="a", role="admin") as c:
        defaults = (await c.get("/api/notifications/preferences")).json()
        assert defaults["invoice_paid"]["email"] is True
        assert defaults["invoice_paid"]["in_app"] is True

        patch = await c.patch(
            "/api/notifications/preferences",
            json={"invoice_paid": {"email": False, "in_app": True}},
        )
        assert patch.status_code == 200
        assert patch.json()["invoice_paid"]["email"] is False
        # Other events untouched (still default-on).
        assert patch.json()["invoice_approved"]["email"] is True

        # Persisted across a fresh request.
        again = (await c.get("/api/notifications/preferences")).json()
        assert again["invoice_paid"]["email"] is False

    # cleanup
    async with ctrl_mk() as s:
        u = (await s.execute(select(User).where(User.id == me))).scalar_one()
        u.notification_prefs = {}
        await s.commit()


async def test_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/notifications")
    assert resp.status_code == 401
