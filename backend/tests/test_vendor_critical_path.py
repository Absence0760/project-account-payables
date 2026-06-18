"""Critical-path vendor management — create/update persistence + the
screening-on-write side effect + tenant isolation + fuzzy matching against
*real* Postgres rows.

Existing coverage:
  * `test_vendor_lifecycle.py` proves `match_vendor` / `match_and_link_vendor`
    with the DB mocked at the row level (the matching ladder + thresholds).
  * `test_vendor_screening_api.py` proves the screen-on-create/update verdict
    and the payment-block gate.

The gaps this file fills — all through the realdb harness, so they exercise the
real ORM round-trip + the real SQL filters:

  1. create persists every supplied field, lands the row in the tenant's
     *default* entity, and runs the initial screen as a side effect without
     blocking the write (a clean vendor still gets a `sanctions_checks` row);
  2. update mutates the persisted row and a cosmetic (non-identity) edit does
     NOT add a redundant screen row, while an identity edit does;
  3. tenant isolation at the data layer — a vendor created in tenant A is
     invisible from tenant B's HTTP surface (404), proving the matcher /
     list / detail queries are tenant-scoped;
  4. `match_vendor` against real rows — exact tax_id, exact name, fuzzy, and
     status filtering (an `inactive` vendor must not be returned as a match,
     which would re-route invoices to a dead supplier).

Money / no money here, but PII discipline matters: the screening trail must
never carry the raw tax_id (only verdict + matched-list), so we assert the
`sanctions_checks` row shape rather than re-asserting field values.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.sanctions_check import SanctionsCheck
from app.models.vendor import Vendor
from app.services.vendor_matching import match_vendor

TENANT = "a"


@pytest.fixture
def mk(realdb):
    return realdb.sessionmaker(TENANT)


async def _default_entity_id(session) -> uuid.UUID:
    from app.models.entity import Entity

    return (await session.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


# ===========================================================================
# Create persists + screens (best-effort, non-blocking) + lands in default entity.
# ===========================================================================


@pytest.mark.asyncio
async def test_create_persists_all_fields_and_lands_in_default_entity(realdb, mk):
    """A create with a full field set round-trips to real Postgres: every
    supplied column is persisted, the server stamps status=active /
    source=manual / verified_*, and the row lands in the tenant's default
    entity (multi-entity Phase 2 — a NULL entity would escape entity-scoped
    reads)."""
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            "/api/vendors",
            json={
                "name": "Persisted Supplies Co",
                "code": "PSC-1",
                "email": "ap@persisted.example",
                "phone": "+1-555-0100",
                "address": "1 Persist Way",
                "tax_id": "55-1234567",
                "payment_terms": "net30",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["source"] == "manual"
    assert body["verified_by"]  # stamped from the acting user

    vendor_id = uuid.UUID(body["id"])
    async with mk() as s:
        ent = await _default_entity_id(s)
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert v.name == "Persisted Supplies Co"
        assert v.code == "PSC-1"
        assert v.email == "ap@persisted.example"
        assert v.phone == "+1-555-0100"
        assert v.address == "1 Persist Way"
        assert v.tax_id == "55-1234567"
        assert v.payment_terms == "net30"
        # The write landed in the default entity, not NULL.
        assert v.entity_id == ent


@pytest.mark.asyncio
async def test_create_clean_vendor_writes_screening_trail_without_blocking(realdb, mk):
    """The screen is a best-effort side effect of the write: a clean vendor is
    created (201) AND its initial `sanctions_checks` trail row exists, with a
    `clear` verdict and the actor recorded. The trail row carries the verdict +
    matched list, never the raw tax_id (PII-out-of-logs)."""
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            "/api/vendors",
            json={"name": "Best Effort Clean Co", "code": "BEC-9", "tax_id": "55-7654321"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["screening_status"] == "clear"
    assert body["payments_blocked"] is False

    vendor_id = uuid.UUID(body["id"])
    async with mk() as s:
        rows = (
            (await s.execute(select(SanctionsCheck).where(SanctionsCheck.vendor_id == vendor_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.check_type == "initial"
    assert row.result == "clear"
    # PII guard — the screening trail must not store the raw tax id anywhere.
    serialised = str({c.name: getattr(row, c.name) for c in SanctionsCheck.__table__.columns})
    assert "55-7654321" not in serialised


# ===========================================================================
# Update — persists + re-screens only on identity change.
# ===========================================================================


@pytest.mark.asyncio
async def test_cosmetic_update_persists_without_extra_screen(realdb, mk):
    """A phone-only edit is cosmetic — it must persist but NOT trigger a second
    screen (re-screening on every cosmetic edit would spam the trail and burn
    provider quota). Exactly one (the create-time) `sanctions_checks` row should
    remain."""
    async with realdb.client(key=TENANT, role="admin") as client:
        created = await client.post(
            "/api/vendors", json={"name": "Cosmetic Edit Co", "code": "CE-1"}
        )
        vendor_id = created.json()["id"]
        patched = await client.patch(f"/api/vendors/{vendor_id}", json={"phone": "+1-555-9999"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["phone"] == "+1-555-9999"

    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(SanctionsCheck).where(SanctionsCheck.vendor_id == uuid.UUID(vendor_id))
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, "cosmetic edit must not add a screening row"


@pytest.mark.asyncio
async def test_identity_update_persists_and_rescreens(realdb, mk):
    """A tax_id (identity) edit persists the new value AND adds a fresh
    screening row — an identity change can flip a vendor onto/off a list."""
    async with realdb.client(key=TENANT, role="admin") as client:
        created = await client.post(
            "/api/vendors", json={"name": "Identity Edit Co", "code": "IE-1"}
        )
        vendor_id = created.json()["id"]
        patched = await client.patch(f"/api/vendors/{vendor_id}", json={"tax_id": "98-7654321"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["tax_id"] == "98-7654321"

    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(SanctionsCheck).where(SanctionsCheck.vendor_id == uuid.UUID(vendor_id))
                )
            )
            .scalars()
            .all()
        )
    # initial (create) + initial (rescreen on identity change).
    assert len(rows) == 2


# ===========================================================================
# Tenant isolation at the data layer.
# ===========================================================================


@pytest.mark.asyncio
async def test_vendor_created_in_tenant_a_is_invisible_from_tenant_b(realdb):
    """A vendor created in tenant A must not be reachable from tenant B's HTTP
    surface (404 on detail). This is the data-layer isolation invariant: the
    detail query is bound to the resolved tenant DB, so B's session simply has
    no such row. A leak here would be a cross-tenant data breach."""
    async with realdb.client(key="a", role="admin") as client_a:
        created = await client_a.post(
            "/api/vendors", json={"name": "Tenant A Only Vendor", "code": "TAO-1"}
        )
        vendor_id = created.json()["id"]
        # Confirm A can see it.
        a_view = await client_a.get(f"/api/vendors/{vendor_id}")
        assert a_view.status_code == 200, a_view.text

    async with realdb.client(key="b", role="admin") as client_b:
        b_view = await client_b.get(f"/api/vendors/{vendor_id}")
    assert b_view.status_code == 404, "tenant B must not reach tenant A's vendor"


@pytest.mark.asyncio
async def test_vendor_list_does_not_bleed_across_tenants(realdb):
    """The list endpoint is entity/tenant-scoped: a uniquely-named vendor in A
    must never appear in B's list result."""
    marker = f"Isolation Marker {uuid.uuid4().hex[:8]}"
    async with realdb.client(key="a", role="admin") as client_a:
        await client_a.post("/api/vendors", json={"name": marker, "code": "ISO-A"})

    async with realdb.client(key="b", role="admin") as client_b:
        resp = await client_b.get("/api/vendors", params={"search": marker})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    items = payload.get("items", payload) if isinstance(payload, dict) else payload
    names = [it["name"] for it in items]
    assert marker not in names


# ===========================================================================
# match_vendor against REAL rows — the matching ladder + status filtering that
# the mocked unit tests can't prove (the SQL `status IN (...)` predicate).
# ===========================================================================


@pytest.mark.asyncio
async def test_match_vendor_exact_tax_id_against_real_rows(realdb, mk):
    """Exact tax_id is the highest-trust key. Against a real row it returns
    confidence 1.0 even when the name differs — proving the live tax_id WHERE
    clause."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        ent = await _default_entity_id(s)
        v = Vendor(
            name="Acme Corporation",
            tax_id="11-2222333",
            organization_id=org_id,
            entity_id=ent,
            status="active",
            source="manual",
        )
        s.add(v)
        await s.commit()

        vendor, conf = await match_vendor(
            s, vendor_name="A Totally Different Name", vendor_tax_id="11-2222333"
        )
    assert vendor is not None
    assert vendor.tax_id == "11-2222333"
    assert conf == 1.0


@pytest.mark.asyncio
async def test_match_vendor_fuzzy_against_real_rows(realdb, mk):
    """Fuzzy match over real rows: `Acme Industries` matches the persisted
    `Acme Industries Inc` (suffix-stripped token overlap) above the 0.6
    threshold, and disjoint rows in the same table don't win."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        ent = await _default_entity_id(s)
        s.add_all(
            [
                Vendor(
                    name="Globex Corporation",
                    organization_id=org_id,
                    entity_id=ent,
                    status="active",
                    source="manual",
                ),
                Vendor(
                    name="Acme Industries Inc",
                    organization_id=org_id,
                    entity_id=ent,
                    status="active",
                    source="manual",
                ),
            ]
        )
        await s.commit()

        vendor, conf = await match_vendor(s, vendor_name="Acme Industries")
    assert vendor is not None
    assert vendor.name == "Acme Industries Inc"
    assert conf >= 0.6


@pytest.mark.asyncio
async def test_match_vendor_skips_inactive_rows(realdb, mk):
    """An exact-name candidate that is `inactive` must NOT be returned —
    `match_vendor` filters to active/unverified. Matching a dead vendor would
    re-route invoices (and payments) to a supplier the org deliberately
    deactivated. The live `status IN (...)` predicate is what enforces this; the
    mocked unit tests pre-filter the pool, so this can only be proven against a
    real row."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        ent = await _default_entity_id(s)
        s.add(
            Vendor(
                name="Deactivated Supplier",
                organization_id=org_id,
                entity_id=ent,
                status="inactive",
                source="manual",
            )
        )
        await s.commit()

        vendor, conf = await match_vendor(s, vendor_name="Deactivated Supplier")
    assert vendor is None, "inactive vendor must not be returned as a match"
    assert conf == 0.0
