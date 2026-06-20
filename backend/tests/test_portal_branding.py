"""Public supplier-portal branding endpoint — `GET /api/portal/branding`.

The supplier-portal login page is unauthenticated and portal users are
`VendorUser` (a different identity from employee `User`s), so the employee-gated
`GET /api/organization/branding` can't serve the portal. This public-by-design
read resolves the tenant via the standard `get_tenant` chokepoint (X-Tenant-Slug
header / custom-domain Host) and returns ONLY the whitelisted `BrandConfig`
fields — never org settings, secrets, or another tenant's brand.

Coverage:
  * Anonymous (no JWT) request succeeds — it's public-by-design.
  * Returns ONLY the whitelisted brand fields (the leakage guard).
  * Fail-soft to all-empty when the tenant has no brand set.
  * A malformed persisted brand block degrades to empty, never 500s.
  * Tenant isolation — tenant A's host returns A's brand, never B's.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.models.organization import Organization
from app.schemas.organization import BrandConfig

pytestmark = pytest.mark.asyncio

# The exact, complete set of keys the public endpoint may ever return. If the
# response carries anything beyond this, a settings field has leaked.
_ALLOWED_KEYS = set(BrandConfig.model_fields.keys())

_ACME_BRAND = {
    "product_name": "Acme Pay",
    "logo_url": "https://cdn.acme.test/logo.png",
    "accent_color": "#112233",
    "accent_strong_color": "#001122",
    "support_url": "https://help.acme.test",
    "legal_url": "https://acme.test/legal",
}


async def _set_org_settings(realdb, key: str, settings: dict) -> None:
    """Write a tenant org's control-plane `settings` JSONB (brand + anything else
    the test wants to prove never leaks)."""
    ctrl_mk = realdb.control_sessionmaker()
    org_id = realdb.info(key).org_id
    async with ctrl_mk() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        org.settings = settings
        flag_modified(org, "settings")
        await s.commit()


async def test_branding_is_public_and_returns_whitelisted_fields_only(realdb):
    """An anonymous (no-JWT) request succeeds and returns the configured brand —
    and ONLY the BrandConfig fields, never the surrounding org settings."""
    await _set_org_settings(
        realdb,
        "a",
        {
            "brand": _ACME_BRAND,
            # Sensitive / unrelated settings that MUST NOT leak through the public
            # read. The response model is BrandConfig, so they can't — this asserts
            # it structurally.
            "payments": {"webhook_secret": "super-secret-do-not-leak"},
            "extraction": {"api_key": "sk-do-not-leak"},
            "sso": {"client_secret": "also-secret"},
        },
    )

    # role=None → no Authorization header → anonymous, the portal-login case.
    async with realdb.client(key="a", role=None) as client:
        resp = await client.get("/api/portal/branding")

    assert resp.status_code == 200
    body = resp.json()

    # The leakage guard: the response keys are EXACTLY the BrandConfig whitelist.
    assert set(body.keys()) == _ALLOWED_KEYS
    # The configured brand round-trips.
    assert body["product_name"] == "Acme Pay"
    assert body["logo_url"] == "https://cdn.acme.test/logo.png"
    assert body["accent_color"] == "#112233"
    assert body["accent_strong_color"] == "#001122"
    assert body["support_url"] == "https://help.acme.test"
    assert body["legal_url"] == "https://acme.test/legal"

    # And nothing sensitive bled in (belt-and-braces over the key-set assert).
    flat = str(body)
    assert "super-secret-do-not-leak" not in flat
    assert "sk-do-not-leak" not in flat
    assert "also-secret" not in flat


async def test_branding_fail_soft_empty_when_unset(realdb):
    """A tenant with no brand block gets all-empty fields (= platform defaults on
    the client) — the portal still themes, never breaks."""
    await _set_org_settings(realdb, "a", {"invoice_defaults": {"currency": "USD"}})

    async with realdb.client(key="a", role=None) as client:
        resp = await client.get("/api/portal/branding")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == _ALLOWED_KEYS
    assert all(v == "" for v in body.values())


async def test_branding_tolerates_malformed_block(realdb):
    """A persisted-but-invalid brand block (e.g. hand-edited bad hex) degrades to
    empty rather than 500-ing the public endpoint."""
    await _set_org_settings(
        realdb,
        "a",
        {"brand": {"accent_color": "not-a-color", "product_name": 12345}},
    )

    async with realdb.client(key="a", role=None) as client:
        resp = await client.get("/api/portal/branding")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == _ALLOWED_KEYS
    assert all(v == "" for v in body.values())


async def test_branding_is_tenant_scoped(realdb):
    """Tenant A's request returns A's brand, never B's — isolation rides the same
    `get_tenant` resolver as every other portal route."""
    await _set_org_settings(realdb, "a", {"brand": {"product_name": "Acme Pay"}})
    await _set_org_settings(realdb, "b", {"brand": {"product_name": "Beta Bill"}})

    async with realdb.client(key="a", role=None) as client_a:
        resp_a = await client_a.get("/api/portal/branding")
    async with realdb.client(key="b", role=None) as client_b:
        resp_b = await client_b.get("/api/portal/branding")

    assert resp_a.json()["product_name"] == "Acme Pay"
    assert resp_b.json()["product_name"] == "Beta Bill"
