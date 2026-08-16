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
    ALIGNMENT_ALIGNED,
    ALIGNMENT_MISALIGNED,
    ALIGNMENT_UNKNOWN,
    DEFAULT_REGION,
    REASON_DEPLOYED_REGION_UNRECOGNISED,
    REASON_DEPLOYED_REGION_UNSET,
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
    assert aligned.status == ALIGNMENT_ALIGNED
    assert aligned.aligned is True
    assert aligned.reason is None

    mismatched = check_residency_alignment(org, "us")
    assert mismatched.status == ALIGNMENT_MISALIGNED
    assert mismatched.aligned is False
    assert mismatched.configured_region == "eu"
    assert mismatched.deployed_region == "us"


def test_alignment_normalizes_the_declared_region():
    """Whitespace / case in the env value is operator sloppiness, not a mismatch."""
    org = _org({"residency": {"region": "eu"}})
    assert check_residency_alignment(org, "  EU \n").status == ALIGNMENT_ALIGNED


@pytest.mark.parametrize("declared", [None, "", "   "])
def test_alignment_is_unknown_when_no_region_is_declared(declared):
    """An unset deployed region must never read as `aligned` — the whole point.

    Defaulting the comparison to DEFAULT_REGION would tell an EU-pinned tenant
    its commitment is honoured on the strength of nobody having said otherwise.
    """
    org = _org({"residency": {"region": "eu"}})
    verdict = check_residency_alignment(org, declared)
    assert verdict.status == ALIGNMENT_UNKNOWN
    assert verdict.aligned is None
    assert verdict.deployed_region is None
    assert verdict.reason == REASON_DEPLOYED_REGION_UNSET
    # Still reports what the tenant asked for — unknown is about US, not them.
    assert verdict.configured_region == "eu"


def test_alignment_is_unknown_when_declared_region_is_unrecognised():
    """A typo'd token (`eu-central-1` for `eu`) reports unknown, not misaligned.

    Comparing literally would mark every tenant misaligned off one bad env
    value, burying the tenants that genuinely are.
    """
    org = _org({"residency": {"region": "us"}})
    verdict = check_residency_alignment(org, "eu-central-1")
    assert verdict.status == ALIGNMENT_UNKNOWN
    assert verdict.aligned is None
    assert verdict.deployed_region is None
    assert verdict.reason == REASON_DEPLOYED_REGION_UNRECOGNISED


def test_alignment_uses_the_default_region_for_an_unpinned_tenant():
    """An org with no residency block is genuinely resident in the default region."""
    verdict = check_residency_alignment(_org({}), DEFAULT_REGION)
    assert verdict.status == ALIGNMENT_ALIGNED
    assert verdict.configured_region == DEFAULT_REGION


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
async def test_get_reports_alignment_against_the_declared_region(realdb, monkeypatch):
    """The advisory verdict reaches the API — the gap this endpoint used to have."""
    from app.config import settings

    monkeypatch.setattr(settings, "deployed_region", "us")

    async with realdb.client(key="a", role="admin") as c:
        await c.put("/api/organization/data-residency", json={"region": "us"})
        resp = await c.get("/api/organization/data-residency")
    assert resp.status_code == 200
    alignment = resp.json()["alignment"]
    assert alignment["status"] == ALIGNMENT_ALIGNED
    assert alignment["aligned"] is True
    assert alignment["deployed_region"] == "us"
    assert alignment["reason"] is None

    # Pinning elsewhere flips the verdict — reported on the PUT response itself,
    # so the admin sees it at the moment they make the commitment.
    async with realdb.client(key="a", role="admin") as c:
        put = await c.put("/api/organization/data-residency", json={"region": "eu"})
    assert put.status_code == 200
    put_alignment = put.json()["alignment"]
    assert put_alignment["status"] == ALIGNMENT_MISALIGNED
    assert put_alignment["aligned"] is False
    assert put_alignment["deployed_region"] == "us"


@pytest.mark.asyncio
async def test_get_reports_unknown_when_no_deployed_region_is_declared(realdb, monkeypatch):
    """No declaration → `unknown` / `null`, never a reassuring `aligned: true`."""
    from app.config import settings

    monkeypatch.setattr(settings, "deployed_region", "")

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get("/api/organization/data-residency")
    assert resp.status_code == 200
    alignment = resp.json()["alignment"]
    assert alignment["status"] == ALIGNMENT_UNKNOWN
    assert alignment["aligned"] is None
    assert alignment["deployed_region"] is None
    assert alignment["reason"] == REASON_DEPLOYED_REGION_UNSET


@pytest.mark.asyncio
async def test_alignment_never_blocks_the_read(realdb, monkeypatch):
    """Advisory means advisory: a misaligned tenant still gets a 200 + its config."""
    from app.config import settings

    monkeypatch.setattr(settings, "deployed_region", "au")

    async with realdb.client(key="a", role="admin") as c:
        await c.put("/api/organization/data-residency", json={"region": "eu"})
        resp = await c.get("/api/organization/data-residency")
    assert resp.status_code == 200
    body = resp.json()
    assert body["region"] == "eu"
    assert body["placement"] == REGION_PLACEMENT["eu"]
    assert body["alignment"]["status"] == ALIGNMENT_MISALIGNED


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
