"""Periodic access reviews — GET /api/access-reviews + POST .../acknowledge.

Covers dormant detection against the threshold, that acknowledge writes the
audit row + stamps the org settings, and the sensitive-read audit row. RBAC
gating itself is covered by test_rbac.py (both routes carry require_roles and are
not in NO_AUTH_REQUIRED). The pure dormancy logic is unit-tested directly against
``compute_access_review`` with a controlled ``now``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.models.organization import Organization
from app.models.user import Role, User, UserRole
from app.models.workflow import AuditLog
from app.services.access_review import compute_access_review


async def _add_audit_row(mk, org_id, actor_id, action, *, when: datetime) -> None:
    async with mk() as s:
        s.add(
            AuditLog(
                correlation_id=uuid.uuid4(),
                organization_id=org_id,
                actor_id=actor_id,
                action=action,
                entity_type="invoice",
                entity_id=uuid.uuid4(),
                created_at=when,
            )
        )
        await s.commit()


# ---------------------------------------------------------------------------
# Pure service: dormancy detection threshold
# ---------------------------------------------------------------------------


async def test_compute_dormant_threshold(realdb):
    """A user whose last mutating action is older than the window is dormant;
    one inside the window is active; one who never acted is dormant."""
    tenant_mk = realdb.sessionmaker("a")
    ctrl_mk = realdb.control_sessionmaker()
    org_id = realdb.info("a").org_id
    users = realdb.info("a").users
    now = datetime(2026, 6, 1, tzinfo=UTC)

    # admin acted 10 days ago (active); ap_manager acted 200 days ago (dormant);
    # cfo never acted (dormant).
    await _add_audit_row(
        tenant_mk, org_id, users["admin"], "invoice.approved", when=now - timedelta(days=10)
    )
    await _add_audit_row(
        tenant_mk, org_id, users["ap_manager"], "payment.created", when=now - timedelta(days=200)
    )

    async with ctrl_mk() as ctrl_db, tenant_mk() as tenant_db:
        rows = await compute_access_review(
            ctrl_db,
            tenant_db,
            organization_id=org_id,
            dormant_after_days=90,
            now=now,
        )

    by_user = {r.user_id: r for r in rows}
    # ap_clerk is NOT elevated → excluded entirely.
    assert users["ap_clerk"] not in by_user

    assert by_user[users["admin"]].dormant is False
    assert by_user[users["admin"]].days_since == 10
    assert by_user[users["ap_manager"]].dormant is True
    assert by_user[users["ap_manager"]].days_since == 200
    assert by_user[users["cfo"]].dormant is True
    assert by_user[users["cfo"]].days_since is None
    assert by_user[users["cfo"]].last_privileged_action_at is None


async def test_compute_read_actions_do_not_reset_dormancy(realdb):
    """A `*.viewed` / `*.exported` read row is NOT evidence of elevated WRITE
    use, so it must not reset the dormancy clock — a user with only read rows is
    still dormant."""
    tenant_mk = realdb.sessionmaker("a")
    ctrl_mk = realdb.control_sessionmaker()
    org_id = realdb.info("a").org_id
    users = realdb.info("a").users
    now = datetime(2026, 6, 1, tzinfo=UTC)

    # admin only ever VIEWED / EXPORTED — recent, but reads don't count.
    await _add_audit_row(
        tenant_mk, org_id, users["admin"], "vendor.viewed", when=now - timedelta(days=1)
    )
    await _add_audit_row(
        tenant_mk, org_id, users["admin"], "audit.exported", when=now - timedelta(days=1)
    )

    async with ctrl_mk() as ctrl_db, tenant_mk() as tenant_db:
        rows = await compute_access_review(
            ctrl_db,
            tenant_db,
            organization_id=org_id,
            dormant_after_days=90,
            now=now,
        )
    admin_row = next(r for r in rows if r.user_id == users["admin"])
    assert admin_row.dormant is True
    assert admin_row.last_privileged_action_at is None


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


async def test_get_access_review_endpoint(realdb):
    """The GET endpoint returns the computed list and writes a sensitive-read
    audit row (`access_review.viewed`)."""
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    users = realdb.info("a").users
    # Give admin a very old action so they're dormant in the response.
    await _add_audit_row(
        tenant_mk,
        org_id,
        users["admin"],
        "invoice.approved",
        when=datetime.now(UTC) - timedelta(days=365),
    )

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get("/api/access-reviews")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dormant_after_days"] == settings.access_review_dormant_days
    assert body["total"] >= 3  # admin + ap_manager + cfo elevated
    user_ids = {u["user_id"] for u in body["users"]}
    assert str(users["admin"]) in user_ids
    assert str(users["ap_clerk"]) not in user_ids  # clerk excluded
    admin_entry = next(u for u in body["users"] if u["user_id"] == str(users["admin"]))
    assert admin_entry["dormant"] is True

    # A `access_review.viewed` audit row was written.
    async with tenant_mk() as s:
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.organization_id == org_id)))
            .scalars()
            .all()
        )
    assert "access_review.viewed" in actions


async def test_acknowledge_writes_audit_and_stamps_settings(realdb):
    """POST .../acknowledge writes an `access_review.completed` audit row AND
    stamps Organization.settings.access_review on the control plane."""
    tenant_mk = realdb.sessionmaker("a")
    ctrl_mk = realdb.control_sessionmaker()
    org_id = realdb.info("a").org_id
    reviewer_id = realdb.info("a").users["cfo"]

    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post("/api/access-reviews/acknowledge")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["acknowledged"] is True
    assert body["reviewer_id"] == str(reviewer_id)

    # Settings stamped on the control-plane org.
    async with ctrl_mk() as s:
        org = await s.get(Organization, org_id)
        ar = (org.settings or {}).get("access_review")
    assert ar is not None
    assert ar["last_completed_by"] == str(reviewer_id)
    assert ar["last_completed_at"]

    # Audit row written to the tenant trail.
    async with tenant_mk() as s:
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.organization_id == org_id)))
            .scalars()
            .all()
        )
    assert "access_review.completed" in actions


async def test_acknowledge_is_idempotent_friendly(realdb):
    """Re-acknowledging just re-stamps — no error, settings reflect the latest."""
    ctrl_mk = realdb.control_sessionmaker()
    org_id = realdb.info("a").org_id

    async with realdb.client(key="a", role="admin") as c:
        first = await c.post("/api/access-reviews/acknowledge")
        second = await c.post("/api/access-reviews/acknowledge")
    assert first.status_code == 200
    assert second.status_code == 200

    async with ctrl_mk() as s:
        org = await s.get(Organization, org_id)
        ar = (org.settings or {}).get("access_review")
    assert ar["last_completed_at"] == second.json()["last_completed_at"]


async def test_access_review_tenant_isolation(realdb):
    """A mutating action by an actor in tenant A must not influence tenant B's
    review (the audit query is org-scoped + runs against the tenant DB)."""
    tenant_mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    users_a = realdb.info("a").users
    now = datetime.now(UTC)
    await _add_audit_row(
        tenant_mk_a, org_a, users_a["admin"], "invoice.approved", when=now - timedelta(days=1)
    )

    # Tenant B's admin has no actions → dormant in B's review.
    users_b = realdb.info("b").users
    async with realdb.client(key="b", role="admin") as c:
        resp = await c.get("/api/access-reviews")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    admin_b = next(u for u in body["users"] if u["user_id"] == str(users_b["admin"]))
    assert admin_b["dormant"] is True
    assert admin_b["last_privileged_action_at"] is None


async def test_custom_role_with_sensitive_permission_is_elevated(realdb):
    """A user holding ONLY a custom role (not one of the 3 system role names)
    that grants `payment.execute` must still be surfaced by the access review
    — the whole point of the granular-permission layer is that a custom role
    can carry fraud-sensitive authority under any name, and a review keyed
    only on role NAME would miss it entirely."""
    from app.api.permissions import PERM_PAYMENT_EXECUTE

    ctrl_mk = realdb.control_sessionmaker()
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    now = datetime(2026, 6, 1, tzinfo=UTC)

    custom_role_id = uuid.uuid4()
    custom_user_id = uuid.uuid4()
    async with ctrl_mk() as s:
        s.add(
            Role(
                id=custom_role_id,
                name="Treasury Ops",
                organization_id=org_id,
                permissions=[PERM_PAYMENT_EXECUTE],
            )
        )
        s.add(
            User(
                id=custom_user_id,
                email=f"treasury-{custom_user_id}@{realdb.info('a').slug}.test",
                full_name="Custom Role Payer",
                hashed_password="x",
                is_active=True,
                organization_id=org_id,
                must_change_password=False,
            )
        )
        await s.flush()
        s.add(UserRole(user_id=custom_user_id, role_id=custom_role_id))
        await s.commit()

    try:
        # Dormant: last mutating action is 200 days before `now`, past the
        # 90-day threshold used elsewhere in this file.
        await _add_audit_row(
            tenant_mk,
            org_id,
            custom_user_id,
            "payment.executed",
            when=now - timedelta(days=200),
        )

        async with ctrl_mk() as ctrl_db, tenant_mk() as tenant_db:
            rows = await compute_access_review(
                ctrl_db,
                tenant_db,
                organization_id=org_id,
                dormant_after_days=90,
                now=now,
            )
        by_user = {r.user_id: r for r in rows}
        assert custom_user_id in by_user, "custom role granting payment.execute must be elevated"
        row = by_user[custom_user_id]
        assert row.roles == ["Treasury Ops"]
        assert row.dormant is True
        assert row.days_since == 200
    finally:
        async with ctrl_mk() as s:
            await s.execute(UserRole.__table__.delete().where(UserRole.user_id == custom_user_id))
            await s.execute(User.__table__.delete().where(User.id == custom_user_id))
            await s.execute(Role.__table__.delete().where(Role.id == custom_role_id))
            await s.commit()


async def test_inactive_elevated_user_excluded(realdb):
    """A deactivated user is dropped from the review (you can't review access for
    someone who can't log in)."""
    ctrl_mk = realdb.control_sessionmaker()
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id

    # Add a fresh inactive admin user.
    inactive_id = uuid.uuid4()
    async with ctrl_mk() as s:
        admin_role_id = (
            await s.execute(
                select(Role.id).where(Role.name == "admin", Role.organization_id.is_(None))
            )
        ).scalar_one()
        s.add(
            User(
                id=inactive_id,
                email=f"inactive-{inactive_id}@{realdb.info('a').slug}.test",
                full_name="Inactive Admin",
                hashed_password="x",
                is_active=False,
                organization_id=org_id,
                must_change_password=False,
            )
        )
        await s.flush()
        s.add(UserRole(user_id=inactive_id, role_id=admin_role_id))
        await s.commit()

    try:
        async with ctrl_mk() as ctrl_db, tenant_mk() as tenant_db:
            rows = await compute_access_review(
                ctrl_db,
                tenant_db,
                organization_id=org_id,
                dormant_after_days=90,
                now=datetime.now(UTC),
            )
        assert inactive_id not in {r.user_id for r in rows}
    finally:
        async with ctrl_mk() as s:
            await s.execute(UserRole.__table__.delete().where(UserRole.user_id == inactive_id))
            await s.execute(User.__table__.delete().where(User.id == inactive_id))
            await s.commit()
