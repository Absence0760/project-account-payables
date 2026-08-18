"""White-label custom-domains admin endpoint (`/api/organization/branding/custom-domains`).

The backend tenant-resolution layer already maps an inbound `Host` to a tenant
via `settings.brand.custom_domains` (see `test_tenant_custom_domain.py`). This
covers the *management* surface for that list:

  * GET — readable by any authed org role (same posture as branding read).
  * PUT — admin-only mutate, normalizes via the resolver's own
    `normalize_custom_domain`, de-dupes, 422 on malformed, persists to
    `settings.brand.custom_domains`, audits `organization.custom_domains_updated`
    PII-free (count only — never the hostnames).
  * Cross-org uniqueness — a host already registered to another tenant is 409
    (the anti-hijack guard).
  * Branding save preserves custom_domains (the latent-wipe regression).

Isolation note: the `realdb` org rows persist across tests in a session (the
control-plane Organization isn't recreated per-test), and `custom_domains` lives
on that row. Every mutating test therefore resets the tenant's list back to `[]`
in a `finally` so it can't leak into another test — order-independent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.models.workflow import AuditLog


async def _reset(realdb, key: str) -> None:
    """Clear a tenant's custom_domains so a mutating test doesn't leak state."""
    async with realdb.client(key=key, role="admin") as c:
        await c.put(
            "/api/organization/branding/custom-domains",
            json={"custom_domains": []},
        )


@pytest.mark.asyncio
async def test_get_custom_domains_any_authed_role(realdb):
    """Read is open to any authenticated org role (the resolver reads it too)."""
    await _reset(realdb, "a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/organization/branding/custom-domains")
    assert resp.status_code == 200
    assert resp.json() == {"custom_domains": []}


@pytest.mark.asyncio
async def test_get_custom_domains_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/organization/branding/custom-domains")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_put_custom_domains_admin_only(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.put(
            "/api/organization/branding/custom-domains",
            json={"custom_domains": ["ap.acme.test"]},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_custom_domains_persists_normalizes_and_audits(realdb):
    # Mixed-case + port + a duplicate — should normalize to a single bare host.
    payload = {"custom_domains": ["AP.Acme.test:7777", "ap.acme.test", "pay.acme.test"]}
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.put("/api/organization/branding/custom-domains", json=payload)
        assert resp.status_code == 200
        assert resp.json()["custom_domains"] == ["ap.acme.test", "pay.acme.test"]

        # Persisted under settings.brand.custom_domains (the resolver's lookup path).
        cmk = realdb.control_sessionmaker()
        async with cmk() as s:
            org = (
                await s.execute(
                    select(Organization).where(Organization.id == realdb.info("a").org_id)
                )
            ).scalar_one()
        assert org.settings["brand"]["custom_domains"] == ["ap.acme.test", "pay.acme.test"]

        # Audit row landed in the tenant trail, PII-free (count only — no hostnames).
        tmk = realdb.sessionmaker("a")
        async with tmk() as s:
            rows = (
                (
                    await s.execute(
                        select(AuditLog).where(
                            AuditLog.action == "organization.custom_domains_updated"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows
        details = rows[-1].details
        assert details["count"]["new"] == 2
        assert "acme.test" not in str(details)
    finally:
        await _reset(realdb, "a")


@pytest.mark.asyncio
async def test_put_custom_domains_round_trips_to_get(realdb):
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["roundtrip.acme.test"]},
            )
        async with realdb.client(key="a", role="ap_clerk") as c:
            resp = await c.get("/api/organization/branding/custom-domains")
        assert resp.status_code == 200
        assert resp.json()["custom_domains"] == ["roundtrip.acme.test"]
    finally:
        await _reset(realdb, "a")


@pytest.mark.asyncio
async def test_put_custom_domains_rejects_malformed(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put(
            "/api/organization/branding/custom-domains",
            json={"custom_domains": ["has space.com"]},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_custom_domains_can_clear(realdb):
    async with realdb.client(key="a", role="admin") as c:
        await c.put(
            "/api/organization/branding/custom-domains",
            json={"custom_domains": ["clearme.acme.test"]},
        )
        resp = await c.put(
            "/api/organization/branding/custom-domains",
            json={"custom_domains": []},
        )
    assert resp.status_code == 200
    assert resp.json()["custom_domains"] == []


@pytest.mark.asyncio
async def test_put_custom_domains_cross_org_uniqueness_409(realdb):
    """Anti-hijack: a host claimed by tenant B can't be registered by tenant A."""
    try:
        # Tenant B claims the host first.
        async with realdb.client(key="b", role="admin") as c:
            resp_b = await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["shared.example.test"]},
            )
        assert resp_b.status_code == 200

        # Tenant A trying the same host is refused.
        async with realdb.client(key="a", role="admin") as c:
            resp_a = await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["shared.example.test"]},
            )
        assert resp_a.status_code == 409
        assert "another tenant" in resp_a.json()["detail"].lower()

        # And tenant A's list is unchanged (the conflicting write didn't partially land).
        async with realdb.client(key="a", role="ap_clerk") as c:
            resp = await c.get("/api/organization/branding/custom-domains")
        assert resp.json()["custom_domains"] == []
    finally:
        await _reset(realdb, "a")
        await _reset(realdb, "b")


@pytest.mark.asyncio
async def test_put_custom_domains_reregistering_own_host_is_ok(realdb):
    """Re-saving a host the tenant already owns is NOT a self-conflict."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["mine.acme.test"]},
            )
            # Save again with the same host (+ a new one) — no 409.
            resp = await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["mine.acme.test", "mine2.acme.test"]},
            )
        assert resp.status_code == 200
        assert resp.json()["custom_domains"] == ["mine.acme.test", "mine2.acme.test"]
    finally:
        await _reset(realdb, "a")


@pytest.mark.asyncio
async def test_branding_save_preserves_custom_domains(realdb):
    """Regression: a branding PUT must NOT wipe registered custom domains
    (custom_domains lives under settings.brand but isn't a BrandConfig field)."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["keepme.acme.test"]},
            )
            # A normal branding save (no custom_domains in the BrandConfig body).
            resp = await c.put(
                "/api/organization/branding",
                json={"product_name": "Acme Pay"},
            )
            assert resp.status_code == 200
            # The custom domains survived the branding write.
            cd = await c.get("/api/organization/branding/custom-domains")
        assert cd.json()["custom_domains"] == ["keepme.acme.test"]
    finally:
        await _reset(realdb, "a")


# ---------------------------------------------------------------------------
# The generic settings PATCH must not be a second, unguarded writer.
#
# `PATCH /api/organization` shallow-merges a free-form settings dict, so
# `{"brand": {"custom_domains": [...]}}` replaced the whole brand key — writing
# the domain list with no normalization, no advisory lock, no cross-org
# uniqueness check and no audit row: every control the dedicated endpoint
# exists to enforce. The same handler already refuses `chat_notifications` for
# exactly this reason.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_organization_settings_refuses_custom_domains(realdb):
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["ours.acme.test"]},
            )
            resp = await c.patch(
                "/api/organization",
                json={"settings": {"brand": {"custom_domains": ["hijacked.acme.test"]}}},
            )
            assert resp.status_code == 422, resp.text
            assert "custom-domains" in resp.json()["detail"]

            # Nothing was written.
            cd = await c.get("/api/organization/branding/custom-domains")
        assert cd.json()["custom_domains"] == ["ours.acme.test"]
    finally:
        await _reset(realdb, "a")


@pytest.mark.asyncio
async def test_patch_organization_settings_cannot_claim_another_tenants_host(realdb):
    """The uniqueness guard the bypass defeated: tenant b registers a host,
    tenant a tries to claim it through the generic settings merge."""
    try:
        async with realdb.client(key="b", role="admin") as c:
            assert (
                await c.put(
                    "/api/organization/branding/custom-domains",
                    json={"custom_domains": ["contested.example.test"]},
                )
            ).status_code == 200

        async with realdb.client(key="a", role="admin") as c:
            resp = await c.patch(
                "/api/organization",
                json={"settings": {"brand": {"custom_domains": ["contested.example.test"]}}},
            )
            assert resp.status_code == 422, resp.text
            # And the dedicated endpoint still refuses it with the 409 it always did.
            dedicated = await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["contested.example.test"]},
            )
            assert dedicated.status_code == 409, dedicated.text
    finally:
        await _reset(realdb, "a")
        await _reset(realdb, "b")


@pytest.mark.asyncio
async def test_patch_organization_settings_brand_preserves_custom_domains(realdb):
    """Refusing the key isn't enough on its own: a brand PATCH that merely
    OMITS custom_domains would still drop every registered host, silently
    un-routing the tenant."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["keepme.acme.test"]},
            )
            resp = await c.patch(
                "/api/organization",
                json={"settings": {"brand": {"product_name": "Acme Pay"}}},
            )
            assert resp.status_code == 200, resp.text
            cd = await c.get("/api/organization/branding/custom-domains")
            assert cd.json()["custom_domains"] == ["keepme.acme.test"]
            # ...and the branding field the PATCH actually meant to set landed.
            brand = (await c.get("/api/organization")).json()["settings"]["brand"]
        assert brand["product_name"] == "Acme Pay"
    finally:
        await _reset(realdb, "a")


@pytest.mark.asyncio
async def test_patch_organization_settings_refuses_non_object_brand_over_domains(realdb):
    """Replacing `brand` with a non-object would drop the domain list with no
    place to put it back — refuse rather than silently un-route the tenant."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["keepme2.acme.test"]},
            )
            resp = await c.patch("/api/organization", json={"settings": {"brand": "nope"}})
            assert resp.status_code == 422, resp.text
            cd = await c.get("/api/organization/branding/custom-domains")
        assert cd.json()["custom_domains"] == ["keepme2.acme.test"]
    finally:
        await _reset(realdb, "a")
