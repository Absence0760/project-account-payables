"""Coverage for external vendor enrichment (firmographics from D&B / Clearbit).

Two tiers, mirroring ``test_vendor_enrichment.py``:

  * Pure-Python / adapter edges — registry + selection, the deterministic mock,
    the real adapters failing CLOSED without a credential, and PII masking. No
    DB, no network.

  * Real-Postgres end-to-end (``realdb``) — drives ``POST
    /api/enrichment/vendors/{id}/enrich`` against a live tenant DB so the SQL,
    RBAC, the advisory (never-overwrites) stance, PII-absence, and tenant
    isolation are all exercised.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.enrichment_adapters import (
    EnrichmentNotConfigured,
    UnknownEnrichmentProviderError,
    VendorEnrichmentQuery,
    get_enrichment_adapter,
)
from app.services.enrichment_adapters.clearbit import ClearbitAdapter
from app.services.enrichment_adapters.dun_bradstreet import DunBradstreetAdapter
from app.services.enrichment_adapters.mock_adapter import MockEnrichmentAdapter

# ---------------------------------------------------------------------------
# Adapter registry + selection
# ---------------------------------------------------------------------------


def test_selector_defaults_to_mock_on_empty_config():
    adapter = get_enrichment_adapter(None)
    assert isinstance(adapter, MockEnrichmentAdapter)
    assert adapter.provider_name == "mock"


def test_selector_refuses_a_named_unknown_provider():
    """A NAMED provider with no registered adapter raises — it never resolves
    to `mock`.

    `mock` fabricates a complete, plausible identity (legal name, registered
    address, DUNS, employee count) with `matched=True`. Falling back to it meant
    a typo in `settings.enrichment.provider` presented invented firmographics to
    a steward as a D&B / Clearbit lookup, one click from being written onto a
    real supplier by `POST .../apply` — where `name` is a screened identity
    field. Same call as `decisions.md` §29 (payments/ERP/FX) and §36 (sanctions).
    """
    with pytest.raises(UnknownEnrichmentProviderError) as exc:
        get_enrichment_adapter({"provider": "totally_made_up"})
    assert exc.value.provider == "totally_made_up"
    # Names the registered alternatives; no credential is echoed.
    assert "mock" in str(exc.value)


def test_selector_still_defaults_to_mock_when_no_provider_is_named():
    """The local-first default is untouched — an org that configured nothing
    still enriches with no cloud account (guard rail 7)."""
    assert isinstance(get_enrichment_adapter({}), MockEnrichmentAdapter)
    assert isinstance(get_enrichment_adapter({"provider": ""}), MockEnrichmentAdapter)


def test_selector_resolves_real_providers_by_name():
    assert isinstance(get_enrichment_adapter({"provider": "dun_bradstreet"}), DunBradstreetAdapter)
    assert isinstance(get_enrichment_adapter({"provider": "clearbit"}), ClearbitAdapter)


# ---------------------------------------------------------------------------
# Mock adapter — deterministic, no network, no credential
# ---------------------------------------------------------------------------


async def test_mock_is_deterministic():
    a = MockEnrichmentAdapter()
    q = VendorEnrichmentQuery(vendor_name="Acme Supplies", vendor_country="US")
    r1 = await a.enrich_vendor(q)
    r2 = await a.enrich_vendor(q)
    assert r1 == r2
    assert r1.matched is True
    assert r1.legal_name == "Acme Supplies (MOCK)"
    assert r1.employee_count is not None
    assert r1.sic_code and r1.naics_code
    # annual_revenue is a string, never a float (money invariant).
    assert isinstance(r1.annual_revenue, str)


async def test_mock_no_match_fixture():
    a = MockEnrichmentAdapter()
    r = await a.enrich_vendor(VendorEnrichmentQuery(vendor_name="Unknown Vendor Fixture"))
    assert r.matched is False
    assert r.legal_name is None


async def test_mock_no_match_override():
    a = MockEnrichmentAdapter({"mock_no_match": ["Mystery Co"]})
    r = await a.enrich_vendor(VendorEnrichmentQuery(vendor_name="mystery co"))
    assert r.matched is False


async def test_mock_masks_tax_id_never_echoes_raw():
    a = MockEnrichmentAdapter()
    r = await a.enrich_vendor(
        VendorEnrichmentQuery(vendor_name="Acme Supplies", vendor_tax_id="12-3456789")
    )
    # Only the masked form is ever returned.
    assert r.tax_id_masked == "***6789"
    # The raw id appears nowhere on the record.
    assert "123456789" not in repr(r)
    assert "12-3456789" not in repr(r)


async def test_mock_test_connection_true():
    assert await MockEnrichmentAdapter().test_connection() is True


# ---------------------------------------------------------------------------
# Real adapters fail closed without a credential (no hardcoded fallback)
# ---------------------------------------------------------------------------


async def test_dun_bradstreet_fails_closed_without_key():
    a = DunBradstreetAdapter({})  # no api_key
    with pytest.raises(EnrichmentNotConfigured):
        await a.enrich_vendor(VendorEnrichmentQuery(vendor_name="Acme"))
    assert await a.test_connection() is False


async def test_clearbit_fails_closed_without_key():
    a = ClearbitAdapter({})  # no api_key
    with pytest.raises(EnrichmentNotConfigured):
        await a.enrich_vendor(VendorEnrichmentQuery(vendor_name="Acme", domain="acme.com"))
    assert await a.test_connection() is False


def test_real_adapters_have_no_hardcoded_key():
    # A bare construct must not smuggle in a default credential.
    assert DunBradstreetAdapter({}).api_key == ""
    assert ClearbitAdapter({}).api_key == ""


# ---------------------------------------------------------------------------
# Real-DB / API tests (realdb fixture)
# ---------------------------------------------------------------------------


async def _seed_vendor(mk, org_id, *, name="Acme Supplies", tax_id=None, email=None):
    from app.models.vendor import Vendor

    async with mk() as s:
        vendor = Vendor(
            organization_id=org_id, name=name, status="active", tax_id=tax_id, email=email
        )
        s.add(vendor)
        await s.commit()
        await s.refresh(vendor)
        return vendor.id


async def test_enrich_endpoint_happy_path_mock(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id, name="Acme Supplies")

    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(f"/api/enrichment/vendors/{vid}/enrich")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["vendor_id"] == str(vid)
    firmo = data["firmographics"]
    assert firmo["provider"] == "mock"
    assert firmo["matched"] is True
    assert firmo["legal_name"] == "Acme Supplies (MOCK)"
    # Suggestion diff present (mock fills an address / website the vendor lacks).
    assert any(s["field"] == "address" for s in data["suggestions"])


async def test_enrich_advisory_never_overwrites_vendor(realdb):
    """The enrichment call must NEVER mutate the Vendor row — advisory only."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id, name="Acme Supplies", email="bob@acme.com")

    async with realdb.client(key="a", role="ap_manager") as client:
        assert (await client.post(f"/api/enrichment/vendors/{vid}/enrich")).status_code == 200

    from app.models.vendor import Vendor

    async with mk() as s:
        v = await s.get(Vendor, vid)
        # Unchanged — address still None, name untouched (no "(MOCK)" leaked in).
        assert v.address is None
        assert v.name == "Acme Supplies"


async def test_enrich_endpoint_no_pii_leak(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id, name="Acme Supplies", tax_id="12-3456789")

    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(f"/api/enrichment/vendors/{vid}/enrich")
    assert r.status_code == 200
    body = r.text
    # Raw tax id never present; only the masked form may appear.
    assert "12-3456789" not in body
    assert "123456789" not in body
    assert r.json()["firmographics"]["tax_id_masked"] == "***6789"


async def test_enrich_unknown_vendor_404(realdb):
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(f"/api/enrichment/vendors/{uuid.uuid4()}/enrich")
    assert r.status_code == 404


async def test_enrich_auth_required(realdb):
    async with realdb.client(key="a", role=None) as client:
        r = await client.post(f"/api/enrichment/vendors/{uuid.uuid4()}/enrich")
    assert r.status_code == 401


async def test_enrich_clerk_forbidden(realdb):
    """External enrichment is a managerial data-stewardship action — clerk 403."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id)
    async with realdb.client(key="a", role="ap_clerk") as clerk:
        assert (await clerk.post(f"/api/enrichment/vendors/{vid}/enrich")).status_code == 403


async def test_enrich_tenant_isolation(realdb):
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    vid = await _seed_vendor(mk_a, org_a)
    async with realdb.client(key="b", role="ap_manager") as client_b:
        r = await client_b.post(f"/api/enrichment/vendors/{vid}/enrich")
    assert r.status_code == 404


async def test_enrich_real_provider_fails_closed_endpoint(realdb):
    """With a per-org real provider but no api_key, the endpoint 422s (fail
    closed) — never silently degrades to fabricated data."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id)

    # Point this org at a real provider with no key via settings.enrichment.
    from sqlalchemy import update

    from app.models.organization import Organization

    control_mk = realdb.control_sessionmaker()
    async with control_mk() as s:
        org = await s.get(Organization, org_id)
        original = dict(org.settings or {})
    try:
        async with control_mk() as s:
            new_settings = dict(original)
            new_settings["enrichment"] = {"provider": "dun_bradstreet"}  # no api_key
            await s.execute(
                update(Organization).where(Organization.id == org_id).values(settings=new_settings)
            )
            await s.commit()

        async with realdb.client(key="a", role="ap_manager") as client:
            r = await client.post(f"/api/enrichment/vendors/{vid}/enrich")
        assert r.status_code == 422
        # PII-free, no key leaked.
        assert "api_key" in r.json()["detail"]
    finally:
        # Restore — the control-plane org row is shared across tests in the
        # session; leaving `settings.enrichment` pointed at a keyless real
        # provider would 422 every other test's enrich call.
        async with control_mk() as s:
            await s.execute(
                update(Organization).where(Organization.id == org_id).values(settings=original)
            )
            await s.commit()
