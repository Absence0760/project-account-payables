"""Partner / reseller multi-tenant admin (`/api/partner`).

A partner (reseller) org administers branded CHILD tenants, linked by the
control-plane self-FK `Organization.parent_org_id` (migration 0065). This covers:

  * Overview — admin-only; lists ONLY the caller's own children
    (`parent_org_id == caller org`); a standalone org gets an empty list +
    `is_partner: false`.
  * Read a child's branding — admin-only; resolves the child's `settings.brand`.
  * Push a child's branding — admin-only; persists to the CHILD's control-plane
    row, preserves `custom_domains`, audits `organization.branding_updated`
    (PII-free, `via: partner`) into the CHILD's tenant trail.
  * Isolation headline — a partner CANNOT read/affect an org it didn't parent
    (opaque 404), and a non-admin is 403, unauthenticated is 401.

Isolation note (mirrors `test_custom_domains_admin.py`): the `realdb` control
Organization rows persist across a session. Each test that mutates the
parent/child link or a child's brand resets that state in a `finally` so it
can't leak — order-independent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.models.organization import Organization
from app.models.workflow import AuditLog


async def _set_parent(realdb, *, child_key: str, parent_key: str | None) -> None:
    """Point tenant `child_key` at `parent_key` (or detach with None)."""
    child_id = realdb.info(child_key).org_id
    parent_id = realdb.info(parent_key).org_id if parent_key is not None else None
    async with realdb.control_sessionmaker()() as s:
        await s.execute(
            update(Organization).where(Organization.id == child_id).values(parent_org_id=parent_id)
        )
        await s.commit()


async def _reset_child_brand(realdb, child_key: str) -> None:
    child_id = realdb.info(child_key).org_id
    async with realdb.control_sessionmaker()() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == child_id))
        ).scalar_one()
        settings = dict(org.settings or {})
        settings.pop("brand", None)
        org.settings = settings
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(org, "settings")
        await s.commit()


@pytest.mark.asyncio
async def test_overview_lists_only_own_children(realdb):
    """Make B a child of A; A's overview lists B and nothing it didn't parent."""
    await _set_parent(realdb, child_key="b", parent_key="a")
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.get("/api/partner")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_partner"] is True
        slugs = {child["slug"] for child in body["children"]}
        assert realdb.info("b").slug in slugs
        # A never parents itself.
        assert realdb.info("a").slug not in slugs
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)


@pytest.mark.asyncio
async def test_overview_standalone_org_is_not_partner(realdb):
    """An org with no children gets an empty list + is_partner:false (no error)."""
    await _set_parent(realdb, child_key="b", parent_key=None)  # ensure detached
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get("/api/partner")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_partner"] is False
    assert body["children"] == []


@pytest.mark.asyncio
async def test_overview_admin_only(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/partner")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_overview_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/partner")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_read_child_branding(realdb):
    await _set_parent(realdb, child_key="b", parent_key="a")
    try:
        child_id = realdb.info("b").org_id
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.get(f"/api/partner/children/{child_id}/branding")
        assert resp.status_code == 200
        # Default (empty) brand — child set nothing yet.
        assert resp.json()["product_name"] == ""
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)


@pytest.mark.asyncio
async def test_non_child_org_is_opaque_404(realdb):
    """ISOLATION HEADLINE: B is NOT parented by A, so A can't read/affect B.

    Same opaque 404 whether the org id is a non-child or unknown — no
    cross-tenant enumeration.
    """
    await _set_parent(realdb, child_key="b", parent_key=None)  # B is standalone
    other_id = realdb.info("b").org_id
    async with realdb.client(key="a", role="admin") as c:
        read = await c.get(f"/api/partner/children/{other_id}/branding")
        write = await c.put(
            f"/api/partner/children/{other_id}/branding",
            json={"product_name": "Hijacked"},
        )
    assert read.status_code == 404
    assert write.status_code == 404
    # B's brand was untouched.
    async with realdb.control_sessionmaker()() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == other_id))
        ).scalar_one()
    assert (org.settings or {}).get("brand", {}).get("product_name") in (None, "")


@pytest.mark.asyncio
async def test_push_child_branding_persists_and_audits(realdb):
    """A pushes branding onto its child B; it lands on B's row + audits B's trail."""
    await _set_parent(realdb, child_key="b", parent_key="a")
    child_id = realdb.info("b").org_id
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.put(
                f"/api/partner/children/{child_id}/branding",
                json={"product_name": "Acme Reseller Pay", "accent_color": "#112233"},
            )
        assert resp.status_code == 200
        assert resp.json()["product_name"] == "Acme Reseller Pay"

        # Persisted on the CHILD's control-plane row.
        async with realdb.control_sessionmaker()() as s:
            org = (
                await s.execute(select(Organization).where(Organization.id == child_id))
            ).scalar_one()
        assert org.settings["brand"]["product_name"] == "Acme Reseller Pay"
        assert org.settings["brand"]["accent_color"] == "#112233"

        # Audited into the CHILD's tenant trail (PII-free, via=partner).
        async with realdb.sessionmaker("b")() as s:
            rows = (
                (
                    await s.execute(
                        select(AuditLog).where(AuditLog.action == "organization.branding_updated")
                    )
                )
                .scalars()
                .all()
            )
        assert rows, "expected a branding_updated audit row on the child trail"
        latest = rows[-1]
        # entity_type matches the child's own branding path so a SOX query on
        # (action, entity_type='organization') sees partner-initiated changes too.
        assert latest.entity_type == "organization"
        assert latest.details.get("via") == "partner"
        assert latest.details.get("product_name_set") is True
        # PII-free: the raw product name is never echoed into the audit detail.
        assert "Acme Reseller Pay" not in str(latest.details)
    finally:
        await _reset_child_brand(realdb, "b")
        await _set_parent(realdb, child_key="b", parent_key=None)


@pytest.mark.asyncio
async def test_push_child_branding_preserves_custom_domains(realdb):
    """A partner brand save must not wipe the child's registered vanity hosts."""
    await _set_parent(realdb, child_key="b", parent_key="a")
    child_id = realdb.info("b").org_id
    try:
        # Seed a custom domain on the child directly.
        async with realdb.control_sessionmaker()() as s:
            org = (
                await s.execute(select(Organization).where(Organization.id == child_id))
            ).scalar_one()
            org.settings = {"brand": {"custom_domains": ["pay.child.example"]}}
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(org, "settings")
            await s.commit()

        async with realdb.client(key="a", role="admin") as c:
            resp = await c.put(
                f"/api/partner/children/{child_id}/branding",
                json={"product_name": "Rebranded"},
            )
        assert resp.status_code == 200

        async with realdb.control_sessionmaker()() as s:
            org = (
                await s.execute(select(Organization).where(Organization.id == child_id))
            ).scalar_one()
        assert org.settings["brand"]["custom_domains"] == ["pay.child.example"]
        assert org.settings["brand"]["product_name"] == "Rebranded"
    finally:
        await _reset_child_brand(realdb, "b")
        await _set_parent(realdb, child_key="b", parent_key=None)


@pytest.mark.asyncio
async def test_push_child_branding_admin_only(realdb):
    await _set_parent(realdb, child_key="b", parent_key="a")
    child_id = realdb.info("b").org_id
    try:
        async with realdb.client(key="a", role="ap_manager") as c:
            resp = await c.put(
                f"/api/partner/children/{child_id}/branding",
                json={"product_name": "Nope"},
            )
        assert resp.status_code == 403
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)
