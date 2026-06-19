"""Data residency (GDPR/CCPA region pinning).

Coverage:

  * Pure resolver — per-org override → platform default, malformed/unknown → default.
  * Placement map + advisory alignment check.
  * Settings endpoint — GET effective region, PUT updates + audit row, RBAC,
    422 on unsupported region.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.workflow import AuditLog
from app.services.data_residency import (
    DEFAULT_REGION,
    REGION_PLACEMENT,
    SUPPORTED_REGIONS,
    check_residency_alignment,
    resolve_region,
)

# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------


def _org(settings):
    return SimpleNamespace(id=uuid.uuid4(), settings=settings)


def test_resolver_falls_back_to_default():
    assert resolve_region(_org({})) == DEFAULT_REGION
    assert resolve_region(_org(None)) == DEFAULT_REGION
    assert resolve_region(None) == DEFAULT_REGION
    assert DEFAULT_REGION in SUPPORTED_REGIONS


def test_resolver_uses_per_org_override():
    assert resolve_region(_org({"residency": {"region": "eu"}})) == "eu"


def test_resolver_unknown_or_malformed_degrades_to_default():
    assert resolve_region(_org({"residency": {"region": "mars"}})) == DEFAULT_REGION
    assert resolve_region(_org({"residency": {"region": None}})) == DEFAULT_REGION
    assert resolve_region(_org({"residency": "not-a-dict"})) == DEFAULT_REGION


def test_every_supported_region_has_placement():
    for region in SUPPORTED_REGIONS:
        placement = REGION_PLACEMENT[region]
        assert placement["db_cluster"]
        assert placement["s3_bucket"]


def test_alignment_check_is_advisory():
    org = _org({"residency": {"region": "eu"}})
    aligned = check_residency_alignment(org, "eu")
    assert aligned["aligned"] is True

    mismatched = check_residency_alignment(org, "us")
    assert mismatched["aligned"] is False
    assert mismatched["configured_region"] == "eu"
    assert mismatched["deployed_region"] == "us"


# ---------------------------------------------------------------------------
# Settings endpoint — real Postgres + ASGI app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_residency_is_internally_consistent(realdb):
    # The tenant DB is shared across tests, so the effective region may have been
    # set by another test; assert the response is internally consistent (region
    # is supported, placement matches that region) rather than a fixed default.
    # The default-fallback behaviour is pinned by the pure-resolver tests above.
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get("/api/organization/data-residency")
    assert resp.status_code == 200
    body = resp.json()
    assert body["region"] in SUPPORTED_REGIONS
    assert body["default_region"] == DEFAULT_REGION
    assert set(body["supported_regions"]) == set(SUPPORTED_REGIONS)
    assert body["placement"] == REGION_PLACEMENT[body["region"]]


@pytest.mark.asyncio
async def test_put_residency_updates_and_audits(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/organization/data-residency", json={"region": "eu"})
    assert resp.status_code == 200
    assert resp.json()["region"] == "eu"

    # Persisted on org settings.
    cmk = realdb.control_sessionmaker()
    from app.models.organization import Organization

    async with cmk() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == realdb.info("a").org_id))
        ).scalar_one()
    assert org.settings["residency"]["region"] == "eu"

    # And an organization.residency_updated audit row landed in the tenant trail.
    tmk = realdb.sessionmaker("a")
    async with tmk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(AuditLog.action == "organization.residency_updated")
                )
            )
            .scalars()
            .all()
        )
    assert rows
    assert rows[-1].details["region"]["new"] == "eu"


@pytest.mark.asyncio
async def test_put_residency_rejects_unsupported_region(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/organization/data-residency", json={"region": "mars"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_residency_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/organization/data-residency")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_residency_update_admin_only(realdb):
    """Read is any authed role; the mutate path is admin-only."""
    async with realdb.client(key="a", role="ap_manager") as c:
        read = await c.get("/api/organization/data-residency")
    assert read.status_code == 200  # non-admin can read

    async with realdb.client(key="a", role="ap_manager") as c:
        write = await c.put("/api/organization/data-residency", json={"region": "eu"})
    assert write.status_code == 403
