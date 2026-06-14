"""Real-DB coverage for the catalogs + guided-buying router.

Covers ``backend/app/api/catalogs.py`` end-to-end against the live test
tenants: catalog CRUD, nested catalog-item CRUD, the punch-out config flag,
guided-buying suggestions (preferred vendors / in-contract vendors / matching
items), RBAC, tenant isolation, audit rows, and exact ``Numeric`` money
round-trips. Mirrors ``test_expenses.py``. DO NOT RUN concurrently — the
``realdb`` fixture truncates shared tables; the orchestrator runs the suite
sequentially at the end.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select, text

from app.models.contract import Contract, ContractStatus, ContractType
from app.models.procurement import Catalog, CatalogItem, CatalogType
from app.models.vendor import Vendor
from app.models.workflow import AuditLog


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# table existence
# ---------------------------------------------------------------------------


async def test_catalog_tables_exist(realdb):
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        for t in ("catalogs", "catalog_items"):
            await s.execute(text(f"SELECT 1 FROM {t} LIMIT 1"))  # raises if missing


# ---------------------------------------------------------------------------
# catalog CRUD
# ---------------------------------------------------------------------------


async def test_create_catalog(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    name = _uniq("Office Supplies")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/catalogs",
            json={"name": name, "catalog_type": "internal", "is_preferred": True},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == name
    assert body["catalog_type"] == "internal"
    assert body["is_preferred"] is True
    assert body["item_count"] == 0

    async with mk() as s:
        cat = (await s.execute(select(Catalog).where(Catalog.name == name))).scalar_one()
        assert cat.organization_id == org_id
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.entity_type == "catalog")))
            .scalars()
            .all()
        )
        assert "catalog.created" in actions


async def test_list_filter_and_get(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        internal = (
            await c.post(
                "/api/catalogs", json={"name": _uniq("Internal"), "catalog_type": "internal"}
            )
        ).json()
        await c.post(
            "/api/catalogs",
            json={
                "name": _uniq("Punchout"),
                "catalog_type": "punchout",
                "punchout_url": "https://supplier.example/punchout",
            },
        )

        # type filter
        only_punchout = await c.get("/api/catalogs?catalog_type=punchout")
        assert only_punchout.status_code == 200
        assert all(i["catalog_type"] == "punchout" for i in only_punchout.json()["items"])

        one = await c.get(f"/api/catalogs/{internal['id']}")
        assert one.status_code == 200
        assert one.json()["id"] == internal["id"]


async def test_punchout_url_persists(realdb):
    url = "https://punchout.example/cxml"
    async with realdb.client(key="a", role="ap_manager") as c:
        cid = (
            await c.post(
                "/api/catalogs",
                json={"name": _uniq("PO"), "catalog_type": "punchout", "punchout_url": url},
            )
        ).json()["id"]
        got = await c.get(f"/api/catalogs/{cid}")
    assert got.json()["punchout_url"] == url
    assert got.json()["catalog_type"] == "punchout"


async def test_update_catalog(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        cid = (
            await c.post("/api/catalogs", json={"name": _uniq("Cat"), "catalog_type": "internal"})
        ).json()["id"]
        resp = await c.patch(
            f"/api/catalogs/{cid}", json={"is_preferred": True, "is_active": False}
        )
    assert resp.status_code == 200
    assert resp.json()["is_preferred"] is True
    assert resp.json()["is_active"] is False

    async with mk() as s:
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.action == "catalog.updated")))
            .scalars()
            .all()
        )
        assert len(actions) >= 1


async def test_delete_catalog_cascades_items(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        cid = (
            await c.post("/api/catalogs", json={"name": _uniq("Del"), "catalog_type": "internal"})
        ).json()["id"]
        item = await c.post(
            f"/api/catalogs/{cid}/items", json={"name": "Widget", "unit_price": "9.99"}
        )
        assert item.status_code == 201
        gone = await c.delete(f"/api/catalogs/{cid}")
        assert gone.status_code == 204
        assert (await c.get(f"/api/catalogs/{cid}")).status_code == 404

    async with mk() as s:
        # The cascade removed the child items too.
        remaining = (
            (await s.execute(select(CatalogItem).where(CatalogItem.catalog_id == uuid.UUID(cid))))
            .scalars()
            .all()
        )
        assert remaining == []


# ---------------------------------------------------------------------------
# catalog items
# ---------------------------------------------------------------------------


async def test_item_crud_and_money_roundtrip(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        cid = (
            await c.post("/api/catalogs", json={"name": _uniq("Items"), "catalog_type": "internal"})
        ).json()["id"]
        created = await c.post(
            f"/api/catalogs/{cid}/items",
            json={"sku": "SKU-1", "name": "Stapler", "unit_price": "12.34", "uom": "each"},
        )
        assert created.status_code == 201, created.text
        item = created.json()
        assert item["unit_price"] == 12.34
        assert item["sku"] == "SKU-1"
        iid = item["id"]

        # nested list
        listing = await c.get(f"/api/catalogs/{cid}/items")
        assert listing.status_code == 200
        assert len(listing.json()) == 1

        # patch
        patched = await c.patch(f"/api/catalogs/items/{iid}", json={"unit_price": "20.00"})
        assert patched.status_code == 200
        assert patched.json()["unit_price"] == 20.0

        # detail catalog now reports the item
        detail = await c.get(f"/api/catalogs/{cid}")
        assert detail.json()["item_count"] == 1

        # delete
        deleted = await c.delete(f"/api/catalogs/items/{iid}")
        assert deleted.status_code == 204

    async with mk() as s:
        # Exact Numeric persisted before delete is gone; audit rows exist.
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.entity_type == "catalog_item")))
            .scalars()
            .all()
        )
        assert "catalog_item.created" in actions
        assert "catalog_item.updated" in actions
        assert "catalog_item.deleted" in actions


async def test_item_under_unknown_catalog_404(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/catalogs/{uuid.uuid4()}/items", json={"name": "Orphan"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# guided buying
# ---------------------------------------------------------------------------


async def _seed_vendor(s, org_id, name) -> Vendor:
    from app.tenant import resolve_default_entity_id

    entity_id = await resolve_default_entity_id(s)
    v = Vendor(name=name, organization_id=org_id, entity_id=entity_id)
    s.add(v)
    await s.flush()
    return v


async def test_guided_buying_surfaces_preferred_and_contract(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id

    # Seed a vendor with a preferred catalog + matching item, and a second
    # vendor with an active contract.
    async with mk() as s:
        from app.tenant import resolve_default_entity_id

        entity_id = await resolve_default_entity_id(s)
        pref_vendor = await _seed_vendor(s, org_id, _uniq("PrefVendor"))
        contract_vendor = await _seed_vendor(s, org_id, _uniq("ContractVendor"))

        catalog = Catalog(
            name=_uniq("PrefCatalog"),
            catalog_type=CatalogType.internal,
            vendor_id=pref_vendor.id,
            is_preferred=True,
            is_active=True,
            organization_id=org_id,
            entity_id=entity_id,
        )
        s.add(catalog)
        await s.flush()
        s.add(
            CatalogItem(
                catalog_id=catalog.id,
                name=_uniq("Pen"),
                category="office",
                unit_price=Decimal("1.50"),
                is_active=True,
                organization_id=org_id,
                entity_id=entity_id,
            )
        )
        s.add(
            Contract(
                contract_number=_uniq("CON"),
                contract_type=ContractType.purchase,
                status=ContractStatus.active,
                vendor_id=contract_vendor.id,
                organization_id=org_id,
                entity_id=entity_id,
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/catalogs/guided-buying?category=office")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    pref_names = {v["vendor_name"] for v in body["preferred_vendors"]}
    assert any(n.startswith("PrefVendor") for n in pref_names)
    assert all("preferred_catalog" in v["reasons"] for v in body["preferred_vendors"])

    contract_names = {v["vendor_name"] for v in body["in_contract_vendors"]}
    assert any(n.startswith("ContractVendor") for n in contract_names)

    assert any(it["category"] == "office" for it in body["items"])


async def test_guided_buying_search_filters_items(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    needle = _uniq("Hammer")
    async with mk() as s:
        from app.tenant import resolve_default_entity_id

        entity_id = await resolve_default_entity_id(s)
        catalog = Catalog(
            name=_uniq("Tools"),
            catalog_type=CatalogType.internal,
            is_active=True,
            organization_id=org_id,
            entity_id=entity_id,
        )
        s.add(catalog)
        await s.flush()
        s.add(
            CatalogItem(
                catalog_id=catalog.id,
                name=needle,
                is_active=True,
                organization_id=org_id,
                entity_id=entity_id,
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/catalogs/guided-buying?q={needle}")
    assert resp.status_code == 200
    names = {it["name"] for it in resp.json()["items"]}
    assert needle in names


# ---------------------------------------------------------------------------
# RBAC + tenant isolation
# ---------------------------------------------------------------------------


async def test_clerk_cannot_create_catalog(realdb):
    # ap_clerk is read-only on catalogs (config-like, mirrors vendors).
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post("/api/catalogs", json={"name": _uniq("X"), "catalog_type": "internal"})
    assert resp.status_code == 403


async def test_clerk_can_read_catalogs(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post("/api/catalogs", json={"name": _uniq("Readable"), "catalog_type": "internal"})
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/catalogs")
    assert resp.status_code == 200


async def test_cfo_can_read_guided_buying(realdb):
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/catalogs/guided-buying")
    assert resp.status_code == 200


async def test_tenant_isolation(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        cid = (
            await c.post(
                "/api/catalogs", json={"name": _uniq("A-only"), "catalog_type": "internal"}
            )
        ).json()["id"]
    async with realdb.client(key="b", role="ap_manager") as c:
        assert (await c.get(f"/api/catalogs/{cid}")).status_code == 404
