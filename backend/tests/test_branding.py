"""White-label / partner branding (per-tenant brand config).

Coverage:

  * BrandConfig schema validation — hex-color + http(s)-URL guards, name strip.
  * GET /api/organization/branding — readable by any authed role, empty default.
  * PUT /api/organization/branding — admin-only mutate, persists to
    settings.brand, audits `organization.branding_updated` (PII-free), 422 on
    bad hex / bad URL, 401 without auth.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.workflow import AuditLog
from app.schemas.organization import BrandConfig

# ---------------------------------------------------------------------------
# Pure schema validation
# ---------------------------------------------------------------------------


def test_brand_defaults_are_empty():
    b = BrandConfig()
    assert b.product_name == ""
    assert b.logo_url == ""
    assert b.accent_color == ""
    assert b.accent_strong_color == ""


def test_brand_accepts_valid_values():
    b = BrandConfig(
        product_name="  Acme Pay  ",
        logo_url="https://cdn.acme.test/logo.png",
        accent_color="#638cff",
        accent_strong_color="#abc",
        support_url="https://help.acme.test",
        legal_url="https://acme.test/legal",
    )
    assert b.product_name == "Acme Pay"  # stripped
    assert b.accent_color == "#638cff"
    assert b.accent_strong_color == "#abc"


@pytest.mark.parametrize("bad", ["638cff", "#zzzzzz", "#12", "rgb(1,2,3)", "red", "#1234"])
def test_brand_rejects_bad_hex(bad):
    with pytest.raises(ValidationError):
        BrandConfig(accent_color=bad)


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "data:text/html,<script>",
        "ftp://x/y",
        "/relative/path",
        "logo.png",
    ],
)
def test_brand_rejects_bad_url(bad):
    with pytest.raises(ValidationError):
        BrandConfig(logo_url=bad)


# ---------------------------------------------------------------------------
# Endpoint — real Postgres + ASGI app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_branding_any_authed_role(realdb):
    """Read is open to any authenticated org role (the app themes itself)."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/organization/branding")
    assert resp.status_code == 200
    body = resp.json()
    # Shape is the full BrandConfig regardless of whether anything is set.
    assert set(body) == {
        "product_name",
        "logo_url",
        "accent_color",
        "accent_strong_color",
        "support_url",
        "legal_url",
    }


@pytest.mark.asyncio
async def test_get_branding_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/organization/branding")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_put_branding_updates_persists_and_audits(realdb):
    payload = {
        "product_name": "Acme Pay",
        "logo_url": "https://cdn.acme.test/logo.png",
        "accent_color": "#112233",
        "accent_strong_color": "#0a1622",
        "support_url": "https://help.acme.test",
        "legal_url": "",
    }
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/organization/branding", json=payload)
    assert resp.status_code == 200
    assert resp.json()["product_name"] == "Acme Pay"
    assert resp.json()["accent_color"] == "#112233"

    # Persisted on org settings.brand.
    cmk = realdb.control_sessionmaker()
    from app.models.organization import Organization

    async with cmk() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == realdb.info("a").org_id))
        ).scalar_one()
    assert org.settings["brand"]["product_name"] == "Acme Pay"
    assert org.settings["brand"]["accent_color"] == "#112233"

    # Audit row landed in the tenant trail, and it is PII-free (booleans only).
    tmk = realdb.sessionmaker("a")
    async with tmk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(AuditLog.action == "organization.branding_updated")
                )
            )
            .scalars()
            .all()
        )
    assert rows
    details = rows[-1].details
    assert details["product_name_set"] is True
    assert details["legal_url_set"] is False
    # No raw branding values leaked into the audit trail.
    assert "Acme Pay" not in str(details)
    assert "cdn.acme.test" not in str(details)


@pytest.mark.asyncio
async def test_put_branding_round_trips_to_get(realdb):
    async with realdb.client(key="a", role="admin") as c:
        await c.put(
            "/api/organization/branding",
            json={"product_name": "Roundtrip Co", "accent_color": "#abcdef"},
        )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/organization/branding")
    assert resp.status_code == 200
    assert resp.json()["product_name"] == "Roundtrip Co"
    assert resp.json()["accent_color"] == "#abcdef"


@pytest.mark.asyncio
async def test_put_branding_rejects_bad_hex(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/organization/branding", json={"accent_color": "blue"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_branding_rejects_bad_url(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/organization/branding", json={"logo_url": "javascript:alert(1)"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_branding_admin_only(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.put("/api/organization/branding", json={"product_name": "Nope"})
    assert resp.status_code == 403
