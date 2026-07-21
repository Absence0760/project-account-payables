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
from decimal import Decimal

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
    # PII: the response masks the raw tax id to `***<last4>` — it is never echoed.
    assert patched.json()["tax_id"] == "***4321"

    async with mk() as s:
        # ...but the raw value is persisted to the row (masking is response-only).
        v = (await s.execute(select(Vendor).where(Vendor.id == uuid.UUID(vendor_id)))).scalar_one()
        assert v.tax_id == "98-7654321"
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


@pytest.mark.asyncio
async def test_masked_tax_id_round_trip_does_not_corrupt_stored_value(realdb, mk):
    """A UI that PATCHes the vendor back with the masked `***<last4>` tax id it
    received must NOT overwrite the stored raw value with the mask. The Update
    schema drops an echoed masked value from the write set."""
    async with realdb.client(key=TENANT, role="admin") as client:
        created = await client.post(
            "/api/vendors",
            json={"name": "Round Trip Co", "code": "RT-1", "tax_id": "12-3456789"},
        )
        vendor_id = created.json()["id"]
        # Create response is already masked.
        assert created.json()["tax_id"] == "***6789"
        # Echo the masked value back on an unrelated edit.
        patched = await client.patch(
            f"/api/vendors/{vendor_id}",
            json={"tax_id": "***6789", "phone": "+1-555-0000"},
        )
    assert patched.status_code == 200, patched.text
    assert patched.json()["phone"] == "+1-555-0000"

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == uuid.UUID(vendor_id)))).scalar_one()
        # Raw value untouched — the mask was never persisted.
        assert v.tax_id == "12-3456789"


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


@pytest.mark.asyncio
async def test_match_vendor_address_only_boosts_never_penalizes(realdb, mk):
    """BUG regression: a perfect fuzzy name match with a STALE (non-matching)
    listed address must keep its high confidence. The old blend
    (`name*0.8 + addr*0.2`) dragged a 1.0 name score down to 0.8 when the
    address disagreed — the address signal must only ever boost, never penalize.

    The input name ("Acme Industries Inc") and the persisted name
    ("Acme Industries Incorporated") both normalize to "acme industries"
    → fuzzy similarity 1.0, but the strings differ so the exact-name
    short-circuit (0.98) is bypassed and the address-blend code is reached.
    """
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        ent = await _default_entity_id(s)
        s.add(
            Vendor(
                name="Acme Industries Incorporated",
                address="123 Old Street, Springfield",
                organization_id=org_id,
                entity_id=ent,
                status="active",
                source="manual",
            )
        )
        await s.commit()

        vendor, conf = await match_vendor(
            s,
            vendor_name="Acme Industries Inc",
            vendor_address="999 New Avenue, Metropolis",  # deliberately disagrees
        )
    assert vendor is not None
    assert vendor.name == "Acme Industries Incorporated"
    # Perfect normalized-name match: the stale address must not erode it.
    # Old (buggy) blend would have produced 0.8; the fix keeps it at 1.0.
    assert conf == 1.0, f"stale address must not penalize a perfect name match (got {conf})"


# ===========================================================================
# GET /api/vendors/counts — status tallies span the WHOLE set, not one page.
# Regression for the filter-chip undercount: the "Unverified" attention badge
# was computed from the loaded page (size 20), so it silently missed
# unverified vendors past page 1.
# ===========================================================================


@pytest.mark.asyncio
async def test_vendor_counts_span_all_pages(realdb, mk):
    """Insert more unverified vendors than fit on one page; the counts endpoint
    must report every one of them (not the 20-row page cap)."""
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        org_id = (await s.execute(select(Vendor.organization_id).limit(1))).scalar()
        if org_id is None:
            # Derive org from the tenant info if the table is empty.
            org_id = realdb.info(TENANT).org_id
        for i in range(25):
            s.add(
                Vendor(
                    name=f"Unverified Co {i:03d}",
                    code=f"UVC{i:03d}",
                    status="unverified",
                    organization_id=org_id,
                    entity_id=entity_id,
                )
            )
        for i in range(3):
            s.add(
                Vendor(
                    name=f"Active Co {i:03d}",
                    code=f"ACT{i:03d}",
                    status="active",
                    organization_id=org_id,
                    entity_id=entity_id,
                )
            )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get("/api/vendors/counts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The 25 unverified vendors are all counted, even though the list page caps
    # at 20 — the chip badge can't undercount anymore.
    assert body["by_status"].get("unverified", 0) >= 25
    assert body["by_status"].get("active", 0) >= 3
    assert body["total"] == sum(body["by_status"].values())


@pytest.mark.asyncio
async def test_vendor_counts_respect_search(realdb, mk):
    """The counts honour the same `search` filter as the list, so the chips
    stay consistent with a filtered table."""
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        org_id = realdb.info(TENANT).org_id
        s.add(
            Vendor(
                name="ZZZ Searchable Unverified",
                code="ZZZSRCH",
                status="unverified",
                organization_id=org_id,
                entity_id=entity_id,
            )
        )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get("/api/vendors/counts?search=ZZZ Searchable")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["by_status"] == {"unverified": 1}
    assert body["total"] == 1


# ===========================================================================
# Entity scoping — `match_vendor` must not reach across subsidiaries.
#
# `vendors` carries a nullable `entity_id` (EntityMixin), so before this was
# scoped an invoice under subsidiary A could be linked to subsidiary B's
# vendor row. That link is load-bearing for money: the credit-memo guard
# compares `invoice.vendor_id`, so a cross-entity mislink lets one
# subsidiary's credit apply against another's payable.
#
# The rule (see `vendor_matching._candidate_query`): candidates are the
# invoice's own entity ∪ rows with a NULL `entity_id` (unstamped/legacy —
# excluding them would silently mint a duplicate vendor rather than fail).
# ===========================================================================


async def _second_entity_id(session, org_id: uuid.UUID) -> uuid.UUID:
    """Create (once) a non-default second subsidiary in this tenant."""
    from app.models.entity import Entity

    existing = (
        await session.execute(select(Entity.id).where(Entity.slug == "sub-b"))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    ent = Entity(name="Subsidiary B", slug="sub-b", organization_id=org_id, is_default=False)
    session.add(ent)
    await session.flush()
    return ent.id


@pytest.mark.asyncio
async def test_match_vendor_does_not_cross_entities_on_tax_id(realdb, mk):
    """A vendor belonging to subsidiary B must NOT be returned for an
    entity-A match, even on the highest-trust key (exact tax_id)."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_a = await _default_entity_id(s)
        entity_b = await _second_entity_id(s, org_id)
        s.add(
            Vendor(
                name="Cross Entity Supplier",
                tax_id="77-7777777",
                organization_id=org_id,
                entity_id=entity_b,
                status="active",
                source="manual",
            )
        )
        await s.commit()

        vendor, conf = await match_vendor(
            s,
            vendor_name="Cross Entity Supplier",
            vendor_tax_id="77-7777777",
            entity_id=entity_a,
        )
    assert vendor is None, "entity-B vendor must not match an entity-A invoice"
    assert conf == 0.0


@pytest.mark.asyncio
async def test_match_vendor_does_not_cross_entities_on_exact_name(realdb, mk):
    """Same rule on the exact-name leg — a partial fix that scoped only the
    tax_id lookup would leave this door open."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_a = await _default_entity_id(s)
        entity_b = await _second_entity_id(s, org_id)
        s.add(
            Vendor(
                name="Nameonly Crossentity Supplier",
                organization_id=org_id,
                entity_id=entity_b,
                status="active",
                source="manual",
            )
        )
        await s.commit()

        vendor, conf = await match_vendor(
            s, vendor_name="Nameonly Crossentity Supplier", entity_id=entity_a
        )
    assert vendor is None
    assert conf == 0.0


@pytest.mark.asyncio
async def test_match_vendor_does_not_cross_entities_on_fuzzy(realdb, mk):
    """And on the fuzzy leg — the third door."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_a = await _default_entity_id(s)
        entity_b = await _second_entity_id(s, org_id)
        s.add(
            Vendor(
                name="Fuzzycross Industries Inc",
                organization_id=org_id,
                entity_id=entity_b,
                status="active",
                source="manual",
            )
        )
        await s.commit()

        vendor, conf = await match_vendor(
            s, vendor_name="Fuzzycross Industries", entity_id=entity_a
        )
    assert vendor is None
    assert conf == 0.0


@pytest.mark.asyncio
async def test_match_vendor_matches_own_entity(realdb, mk):
    """The control for the three tests above: the SAME lookups still match a
    vendor that does belong to the invoice's entity. Without this, a test
    suite that only asserts non-matching would pass on a matcher that matches
    nothing at all."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_a = await _default_entity_id(s)
        await _second_entity_id(s, org_id)
        s.add(
            Vendor(
                name="Ownentity Supplier",
                tax_id="66-6666666",
                organization_id=org_id,
                entity_id=entity_a,
                status="active",
                source="manual",
            )
        )
        await s.commit()

        by_tax, tax_conf = await match_vendor(
            s, vendor_name="Whatever", vendor_tax_id="66-6666666", entity_id=entity_a
        )
        by_name, name_conf = await match_vendor(
            s, vendor_name="Ownentity Supplier", entity_id=entity_a
        )
    assert by_tax is not None and tax_conf == 1.0
    assert by_name is not None and name_conf == 0.98


@pytest.mark.asyncio
async def test_match_vendor_matches_unstamped_null_entity_vendor(realdb, mk):
    """A vendor with a NULL `entity_id` — pre-multi-entity or created from an
    entity-less invoice — must stay matchable from any entity. Dropping these
    would not fail loudly; it would duplicate the supplier (splitting spend
    rollups and creating a second, independently editable bank-detail row)."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_a = await _default_entity_id(s)
        s.add(
            Vendor(
                name="Unstamped Legacy Supplier",
                tax_id="44-4444444",
                organization_id=org_id,
                entity_id=None,
                status="active",
                source="manual",
            )
        )
        await s.commit()

        by_tax, tax_conf = await match_vendor(
            s, vendor_name="Anything", vendor_tax_id="44-4444444", entity_id=entity_a
        )
        by_name, name_conf = await match_vendor(
            s, vendor_name="Unstamped Legacy Supplier", entity_id=entity_a
        )
        by_fuzzy, fuzzy_conf = await match_vendor(
            s, vendor_name="Unstamped Legacy Supplier Inc", entity_id=entity_a
        )
    assert by_tax is not None and tax_conf == 1.0
    assert by_name is not None and name_conf == 0.98
    assert by_fuzzy is not None and fuzzy_conf >= 0.6


@pytest.mark.asyncio
async def test_match_vendor_prefers_own_entity_over_unstamped_duplicate(realdb, mk):
    """When the same supplier exists both unstamped (NULL) and under the
    invoice's own entity, the entity's own row wins — and a duplicated tax_id
    resolves to one row instead of raising MultipleResultsFound (which would
    turn invoice creation into a 500)."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_a = await _default_entity_id(s)
        s.add(
            Vendor(
                name="Dupetax Supplier Legacy",
                tax_id="33-3333333",
                organization_id=org_id,
                entity_id=None,
                status="active",
                source="manual",
            )
        )
        await s.flush()
        owned = Vendor(
            name="Dupetax Supplier Owned",
            tax_id="33-3333333",
            organization_id=org_id,
            entity_id=entity_a,
            status="active",
            source="manual",
        )
        s.add(owned)
        await s.commit()
        owned_id = owned.id

        vendor, conf = await match_vendor(
            s, vendor_name="Dupetax Supplier", vendor_tax_id="33-3333333", entity_id=entity_a
        )
    assert vendor is not None and conf == 1.0
    assert vendor.id == owned_id, "the invoice's own entity must outrank an unstamped row"


@pytest.mark.asyncio
async def test_match_vendor_unscoped_call_is_unchanged(realdb, mk):
    """`entity_id=None` — an unstamped invoice, or a caller with no entity in
    hand — is a passthrough that still searches the whole tenant. This is the
    pre-multi-entity contract and the reason single-entity tenants see no
    behaviour change."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_b = await _second_entity_id(s, org_id)
        s.add(
            Vendor(
                name="Unscoped Reachable Supplier",
                organization_id=org_id,
                entity_id=entity_b,
                status="active",
                source="manual",
            )
        )
        await s.commit()

        vendor, conf = await match_vendor(s, vendor_name="Unscoped Reachable Supplier")
    assert vendor is not None
    assert conf == 0.98


@pytest.mark.asyncio
async def test_match_vendor_single_entity_tenant_behaviour_unchanged(realdb, mk):
    """The overwhelmingly common case: one (default) entity. Every vendor sits
    under it, so scoping to `default ∪ NULL` admits the whole table and the
    full ladder — tax_id, exact name, fuzzy, and the inactive filter — behaves
    exactly as it did before scoping."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_a = await _default_entity_id(s)
        s.add_all(
            [
                Vendor(
                    name="Singleent Taxkey Supplier",
                    tax_id="22-2222222",
                    organization_id=org_id,
                    entity_id=entity_a,
                    status="active",
                    source="manual",
                ),
                Vendor(
                    name="Singleent Fuzzy Industries Inc",
                    organization_id=org_id,
                    entity_id=entity_a,
                    status="active",
                    source="manual",
                ),
                Vendor(
                    name="Singleent Dead Supplier",
                    organization_id=org_id,
                    entity_id=entity_a,
                    status="inactive",
                    source="manual",
                ),
            ]
        )
        await s.commit()

        by_tax, tax_conf = await match_vendor(
            s, vendor_name="Unrelated", vendor_tax_id="22-2222222", entity_id=entity_a
        )
        by_name, name_conf = await match_vendor(
            s, vendor_name="singleent taxkey supplier", entity_id=entity_a
        )
        by_fuzzy, fuzzy_conf = await match_vendor(
            s, vendor_name="Singleent Fuzzy Industries", entity_id=entity_a
        )
        dead, dead_conf = await match_vendor(
            s, vendor_name="Singleent Dead Supplier", entity_id=entity_a
        )
    assert by_tax is not None and tax_conf == 1.0
    assert by_name is not None and name_conf == 0.98
    assert by_fuzzy is not None and by_fuzzy.name == "Singleent Fuzzy Industries Inc"
    assert fuzzy_conf >= 0.6
    assert dead is None and dead_conf == 0.0, "inactive filter must still apply"


@pytest.mark.asyncio
async def test_match_and_link_vendor_uses_the_invoices_entity(realdb, mk):
    """End of the chain: `match_and_link_vendor` derives the entity from the
    invoice, so no call site has to know about it. An entity-B invoice must
    NOT be linked to an entity-A vendor — it gets its own unverified row,
    stamped with entity B.

    This is also what makes an inter-company *mirror* payable correct: the
    mirror sits under the counterparty entity, so it matches against the
    counterparty's vendors with no call-site changes.
    """
    from app.models.invoice import Invoice
    from app.services.vendor_matching import match_and_link_vendor

    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_a = await _default_entity_id(s)
        entity_b = await _second_entity_id(s, org_id)
        a_vendor = Vendor(
            name="Shared Name Supplier",
            organization_id=org_id,
            entity_id=entity_a,
            status="active",
            source="manual",
        )
        s.add(a_vendor)
        await s.flush()
        a_vendor_id = a_vendor.id

        invoice = Invoice(
            organization_id=org_id,
            entity_id=entity_b,
            invoice_number="ENT-SCOPE-1",
            vendor_name="Shared Name Supplier",
            amount=Decimal("100.00"),
            currency="USD",
            status="new",
        )
        s.add(invoice)
        await s.flush()

        vendor, action = await match_and_link_vendor(s, invoice, org_id, source="manual")
        await s.commit()

        assert action == "created", "must not link across entities"
        assert vendor.id != a_vendor_id
        assert vendor.entity_id == entity_b
        assert invoice.vendor_id == vendor.id
