"""Entity (legal entity / subsidiary) tests — multi-entity Phase 1.

Real-Postgres harness (`realdb`): the two test tenants each start every test
with exactly one Default entity (seeded by conftest after the per-test
TRUNCATE). Covers the `/api/entities` CRUD surface, the single-default and
unique-slug DB constraints, RBAC, and tenant isolation.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.entity import Entity

# ---------------------------------------------------------------------------
# Baseline: every tenant starts with one Default entity
# ---------------------------------------------------------------------------


async def test_default_entity_exists_per_tenant(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/entities")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["is_default"] is True
    assert rows[0]["slug"] == "default"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_create_entity_happy_path(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/entities", json={"name": "US Inc", "slug": "us", "currency": "usd"}
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "us"
    assert body["currency"] == "USD"  # normalized upper
    assert body["is_default"] is False

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        count = (
            await s.execute(select(func.count()).select_from(Entity).where(Entity.slug == "us"))
        ).scalar_one()
    assert count == 1


async def test_create_entity_rejects_bad_slug(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/entities", json={"name": "Bad", "slug": "Has Spaces"})
    assert resp.status_code == 400


async def test_create_entity_rejects_duplicate_slug(realdb):
    async with realdb.client(key="a", role="admin") as c:
        first = await c.post("/api/entities", json={"name": "UK Ltd", "slug": "uk"})
        assert first.status_code == 201
        dup = await c.post("/api/entities", json={"name": "UK Two", "slug": "uk"})
    assert dup.status_code == 409


async def test_create_entity_requires_admin(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/entities", json={"name": "Nope", "slug": "nope"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def test_update_entity_rename_and_currency(realdb):
    async with realdb.client(key="a", role="admin") as c:
        created = await c.post("/api/entities", json={"name": "Old", "slug": "ent1"})
        eid = created.json()["id"]
        resp = await c.patch(f"/api/entities/{eid}", json={"name": "New Name", "currency": "eur"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New Name"
    assert body["currency"] == "EUR"


async def test_cannot_deactivate_default_entity(realdb):
    async with realdb.client(key="a", role="admin") as c:
        listing = await c.get("/api/entities")
        default_id = listing.json()[0]["id"]
        resp = await c.patch(f"/api/entities/{default_id}", json={"is_active": False})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DB constraints
# ---------------------------------------------------------------------------


async def test_single_default_entity_enforced(realdb):
    """uq_entities_one_default makes a second default impossible."""
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        s.add(
            Entity(
                organization_id=realdb.info("a").org_id,
                name="Second Default",
                slug="second-default",
                is_default=True,
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()


async def test_entity_slug_unique(realdb):
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        s.add(Entity(organization_id=realdb.info("a").org_id, name="Dup", slug="default"))
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_entities_are_tenant_isolated(realdb):
    async with realdb.client(key="a", role="admin") as c:
        await c.post("/api/entities", json={"name": "A-only", "slug": "a-only"})

    async with realdb.client(key="b", role="admin") as c:
        resp = await c.get("/api/entities")
    slugs = {e["slug"] for e in resp.json()}
    assert "a-only" not in slugs  # tenant b never sees tenant a's entity
