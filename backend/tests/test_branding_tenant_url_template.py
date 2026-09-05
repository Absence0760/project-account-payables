"""Per-tenant vanity base URL (`settings.brand.tenant_url_template`) + the
platform-domain custom-domain guard.

Two halves of the same white-label problem:

  * **The override** — a tenant reachable at its own hostname needs every
    outbound link (invites, password resets, portal + approval deep links)
    built from THAT host, not `<slug>.<platform-domain>`. The value is managed
    on the existing branding endpoint (GET any authed role, admin-only mutate,
    audited PII-free) and read by `app/utils/tenant_urls.tenant_base_url` —
    resolution rules live in `tests/test_tenant_url_resolver.py`.
  * **The guard** — a "custom domain" that sits UNDER the platform's own domain
    is already routed by the `<slug>.<platform-domain>` subdomain path, so
    registering it hands two resolvers a conflicting claim on one hostname
    (and lets one tenant register a name another tenant's slug owns, or will
    own at the next signup). Refused at registration.

Isolation note: the control-plane `Organization` row persists across tests in a
session, and `settings.brand` lives on it — so every mutating test restores the
brand block in a `finally`, exactly like `test_custom_domains_admin.py` does
for `custom_domains`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import settings as app_settings
from app.models.organization import Organization
from app.models.workflow import AuditLog
from app.utils.tenant_urls import tenant_base_url

EMPTY_BRAND = {
    "product_name": "",
    "logo_url": "",
    "accent_color": "",
    "accent_strong_color": "",
    "support_url": "",
    "legal_url": "",
    "tenant_url_template": "",
}


async def _reset_brand(realdb, key: str) -> None:
    async with realdb.client(key=key, role="admin") as c:
        await c.put("/api/organization/branding", json=EMPTY_BRAND)


async def _reset_domains(realdb, key: str) -> None:
    async with realdb.client(key=key, role="admin") as c:
        await c.put("/api/organization/branding/custom-domains", json={"custom_domains": []})


# ---------------------------------------------------------------------------
# GET / PUT the override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branding_get_exposes_the_field_and_defaults_empty(realdb):
    """Empty means "use the platform template" — the whole-app default."""
    await _reset_brand(realdb, "a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/organization/branding")
    assert resp.status_code == 200
    assert resp.json()["tenant_url_template"] == ""


@pytest.mark.asyncio
async def test_put_tenant_url_template_is_admin_only(realdb):
    """A standing instruction about where every invite and reset link points is
    a security control, not a display preference — same gate as the rest of
    branding."""
    for role in ("ap_manager", "cfo", "ap_clerk"):
        async with realdb.client(key="a", role=role) as c:
            resp = await c.put(
                "/api/organization/branding",
                json={**EMPTY_BRAND, "tenant_url_template": "https://ap.acmecorp.test"},
            )
        assert resp.status_code == 403, role


@pytest.mark.asyncio
async def test_put_persists_audits_and_drives_the_resolver(realdb):
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.put(
                "/api/organization/branding",
                json={**EMPTY_BRAND, "tenant_url_template": "https://ap.acmecorp.test/"},
            )
        assert resp.status_code == 200
        # Trailing slash survives the round trip (it is a stored config value);
        # the RESOLVER is what normalises it off, so links can't come out `//`.
        assert resp.json()["tenant_url_template"] == "https://ap.acmecorp.test/"

        cmk = realdb.control_sessionmaker()
        async with cmk() as s:
            org = (
                await s.execute(
                    select(Organization).where(Organization.id == realdb.info("a").org_id)
                )
            ).scalar_one()
        assert org.settings["brand"]["tenant_url_template"] == "https://ap.acmecorp.test/"
        # The whole point: the resolver now answers with the vanity host.
        assert tenant_base_url(org.slug, org.settings) == "https://ap.acmecorp.test"

        # Audited PII-free — a boolean like its sibling fields, never the URL.
        tmk = realdb.sessionmaker("a")
        async with tmk() as ts:
            rows = (
                (
                    await ts.execute(
                        select(AuditLog).where(AuditLog.action == "organization.branding_updated")
                    )
                )
                .scalars()
                .all()
            )
        assert rows, "branding update must write an audit row"
        details = rows[-1].details or {}
        assert details.get("tenant_url_template_set") is True
        assert "acmecorp" not in str(details)
    finally:
        await _reset_brand(realdb, "a")


@pytest.mark.asyncio
async def test_null_clears_the_override(realdb):
    """The field is nullable on the wire — clearing it in the UI sends `null`,
    which must mean "fall back to the platform template", not a 422."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/branding",
                json={**EMPTY_BRAND, "tenant_url_template": "https://ap.acmecorp.test"},
            )
            resp = await c.put(
                "/api/organization/branding",
                json={**EMPTY_BRAND, "tenant_url_template": None},
            )
        assert resp.status_code == 200
        assert resp.json()["tenant_url_template"] == ""
    finally:
        await _reset_brand(realdb, "a")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "ftp://ap.acmecorp.test",
        "ap.acmecorp.test",
        "//ap.acmecorp.test",
        "https://ap.acmecorp.test/a\nb",
        "https://ap.acme corp.test",
        "https://" + "a" * 4000,
    ],
)
async def test_invalid_urls_are_rejected(realdb, bad):
    """Same http(s) shape rule the sibling brand URLs use — one helper, so a
    field can't drift onto a looser definition of "is this a URL"."""
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put(
            "/api/organization/branding",
            json={**EMPTY_BRAND, "tenant_url_template": bad},
        )
    assert resp.status_code == 422, bad


@pytest.mark.asyncio
async def test_branding_put_preserves_custom_domains(realdb):
    """`custom_domains` lives under `settings.brand` but is NOT a BrandConfig
    field, so a naive whole-key replace wipes a tenant's vanity hostnames. It
    survived before this field existed; it must keep surviving."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            r = await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["ap.acmecorp.test"]},
            )
            assert r.status_code == 200
            r = await c.put(
                "/api/organization/branding",
                json={**EMPTY_BRAND, "tenant_url_template": "https://ap.acmecorp.test"},
            )
            assert r.status_code == 200
            r = await c.get("/api/organization/branding/custom-domains")
        assert r.json()["custom_domains"] == ["ap.acmecorp.test"]
    finally:
        await _reset_domains(realdb, "a")
        await _reset_brand(realdb, "a")


@pytest.mark.asyncio
async def test_public_portal_branding_does_not_publish_the_override(realdb):
    """`GET /portal/branding` is public-by-design and returns a `BrandConfig`,
    so adding a field to that model silently widens an UNAUTHENTICATED surface.

    The vanity base URL is not a theming value and nothing on the portal login
    page consumes it — and an admin may have configured it while the DNS
    cutover is still staged — so the portal blanks it.
    """
    try:
        async with realdb.client(key="a", role="admin") as c:
            r = await c.put(
                "/api/organization/branding",
                json={
                    **EMPTY_BRAND,
                    "product_name": "Acme Pay",
                    "tenant_url_template": "https://staged.acmecorp.test",
                },
            )
            assert r.status_code == 200

        async with realdb.client(key="a", role=None) as c:
            resp = await c.get("/api/portal/branding")
        assert resp.status_code == 200
        body = resp.json()
        # The theming fields still come through…
        assert body["product_name"] == "Acme Pay"
        # …but the vanity URL does not.
        assert body["tenant_url_template"] == ""
        assert "staged.acmecorp.test" not in resp.text
    finally:
        await _reset_brand(realdb, "a")


# ---------------------------------------------------------------------------
# Custom-domain platform-domain guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_domain_under_the_platform_domain_is_refused(realdb, monkeypatch):
    """`acme.app.example.com` already resolves via the subdomain path. Claiming
    it as a custom domain shadows whichever tenant owns (or later takes) that
    slug, so it is refused at registration — the cheap place to fix it."""
    monkeypatch.setattr(app_settings, "tenant_url_template", "https://{slug}.app.example.com")
    try:
        async with realdb.client(key="a", role="admin") as c:
            for host in (
                "app.example.com",  # the platform apex itself
                "victim.app.example.com",  # another tenant's subdomain
                "AP.App.Example.Com:443",  # normalisation must not sneak it past
                "deep.sub.app.example.com",
            ):
                resp = await c.put(
                    "/api/organization/branding/custom-domains",
                    json={"custom_domains": [host]},
                )
                assert resp.status_code == 422, host
                # PII-free / value-free: never echoes the submitted host.
                assert host.split(":")[0] not in resp.json()["detail"]

            # …and nothing was persisted by the refused calls.
            resp = await c.get("/api/organization/branding/custom-domains")
        assert resp.json()["custom_domains"] == []
    finally:
        await _reset_domains(realdb, "a")


@pytest.mark.asyncio
async def test_a_legitimate_vanity_host_still_passes(realdb, monkeypatch):
    """The guard must not become a general-purpose refusal — a genuine vanity
    host, including one whose name merely ENDS with the platform domain's text
    without a label boundary, is exactly what the feature is for."""
    monkeypatch.setattr(app_settings, "tenant_url_template", "https://{slug}.app.example.com")
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.put(
                "/api/organization/branding/custom-domains",
                json={"custom_domains": ["ap.acmecorp.test", "notapp.example.com"]},
            )
        assert resp.status_code == 200
        assert resp.json()["custom_domains"] == ["ap.acmecorp.test", "notapp.example.com"]
    finally:
        await _reset_domains(realdb, "a")
