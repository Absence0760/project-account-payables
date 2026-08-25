"""Real-DB coverage for the vendor bulk-operation endpoints.

`/api/vendors/ids` (the "select all N matching" resolver), `/bulk/status`
(bulk verify/reject), `/bulk/screen` (bulk re-screen), and `/bulk/export`
(CSV) — the power-user volume affordances `/vendors` shipped without despite
being one of the five primary list pages. Each mutating bulk endpoint shares
the SAME skip-and-report partial-success contract as the sibling invoice /
expense bulk endpoints: one bad id in a batch of many must never roll back
the rest.
"""

import uuid

from sqlalchemy import select

from app.models.vendor import Vendor
from app.models.workflow import AuditLog

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    row = (
        await s.execute(select(Entity.id).where(Entity.is_default.is_(True)))
    ).scalar_one_or_none()
    return row


async def _add_vendor(mk, org_id, name="Bulk Co", status="unverified") -> str:
    async with mk() as s:
        ent = await _default_entity_id(s)
        v = Vendor(organization_id=org_id, entity_id=ent, name=name, status=status, source="manual")
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return str(v.id)


# ---------------------------------------------------------------------------
# GET /vendors/ids
# ---------------------------------------------------------------------------


async def test_vendor_ids_matches_list_filters(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    matching = [await _add_vendor(mk, org_id, name=f"Widgets Co {i}") for i in range(3)]
    await _add_vendor(mk, org_id, name="Other Supplier")

    async with realdb.client(key=TENANT) as c:
        resp = await c.get("/api/vendors/ids", params={"search": "Widgets"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert set(body["ids"]) == set(matching)


async def test_vendor_ids_exceeds_single_page(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    created = [await _add_vendor(mk, org_id, name=f"Volume Co {i}") for i in range(25)]

    async with realdb.client(key=TENANT) as c:
        resp = await c.get("/api/vendors/ids", params={"search": "Volume Co"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 25
    assert body["truncated"] is False
    assert set(body["ids"]) == set(created)


# ---------------------------------------------------------------------------
# POST /vendors/bulk/status
# ---------------------------------------------------------------------------


async def test_bulk_verify_updates_and_audits(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    actor_id = realdb.info(TENANT).users["admin"]
    ids = [await _add_vendor(mk, org_id, name=f"Verify Co {i}") for i in range(3)]

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post("/api/vendors/bulk/status", json={"ids": ids, "status": "active"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 3
    assert body["skipped"] == []

    async with mk() as s:
        rows = (await s.execute(select(Vendor).where(Vendor.id.in_(ids)))).scalars().all()
        assert all(v.status == "active" for v in rows)
        audit_rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "vendor.verified",
                        AuditLog.entity_id.in_(ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_rows) == 3
        assert all(r.actor_id == actor_id for r in audit_rows)


async def test_bulk_status_skips_bad_and_illegal_rows_not_the_whole_batch(realdb):
    """Same partial-success contract as the invoice/expense bulk endpoints:
    a stale id and a vendor already past the legal starting status for the
    target are each skipped-and-reported — the other, valid, row in the
    SAME batch still lands."""
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    good_id = await _add_vendor(mk, org_id, name="Good Co", status="unverified")
    already_active_id = await _add_vendor(mk, org_id, name="Already Active Co", status="active")
    missing_id = str(uuid.uuid4())

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(
            "/api/vendors/bulk/status",
            json={"ids": [good_id, already_active_id, missing_id], "status": "active"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 1
    skipped_ids = {s["id"] for s in body["skipped"]}
    assert skipped_ids == {already_active_id, missing_id}

    async with mk() as s:
        good = (await s.execute(select(Vendor).where(Vendor.id == good_id))).scalar_one()
        assert good.status == "active"


async def test_bulk_status_rejects_out_of_scope_target(realdb):
    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(
            "/api/vendors/bulk/status", json={"ids": [str(uuid.uuid4())], "status": "inactive"}
        )
    # `inactive` isn't in `VendorBulkStatusTarget` — refused by Pydantic
    # before the endpoint even runs.
    assert resp.status_code == 422, resp.text


async def test_bulk_status_requires_vendor_manage_permission(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    vid = await _add_vendor(mk, org_id)

    async with realdb.client(key=TENANT, role="ap_clerk") as c:
        resp = await c.post("/api/vendors/bulk/status", json={"ids": [vid], "status": "active"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /vendors/bulk/screen
# ---------------------------------------------------------------------------


async def test_bulk_screen_updates_screening_status(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    ids = [await _add_vendor(mk, org_id, name=f"Screen Co {i}") for i in range(2)]

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post("/api/vendors/bulk/screen", json={"ids": ids})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["screened"] == 2
    assert body["skipped"] == []

    async with mk() as s:
        rows = (await s.execute(select(Vendor).where(Vendor.id.in_(ids)))).scalars().all()
        assert all(v.screening_status != "unscreened" for v in rows)


async def test_bulk_screen_skips_unresolvable_id(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    good_id = await _add_vendor(mk, org_id, name="Screenable Co")
    missing_id = str(uuid.uuid4())

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post("/api/vendors/bulk/screen", json={"ids": [good_id, missing_id]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["screened"] == 1
    assert [s["id"] for s in body["skipped"]] == [missing_id]


# ---------------------------------------------------------------------------
# POST /vendors/bulk/export
# ---------------------------------------------------------------------------


async def test_bulk_export_returns_csv_without_bank_details(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    ids = [await _add_vendor(mk, org_id, name=f"Export Co {i}", status="active") for i in range(2)]

    async with realdb.client(key=TENANT) as c:
        resp = await c.post("/api/vendors/bulk/export", json={"ids": ids})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.text
    assert "Export Co 0" in text
    assert "Export Co 1" in text
    # PII/banking-data-out-of-exports invariant: never a bank field.
    assert "account_number" not in text
    assert "routing_number" not in text


async def test_bulk_export_unknown_ids_404(realdb):
    async with realdb.client(key=TENANT) as c:
        resp = await c.post("/api/vendors/bulk/export", json={"ids": [str(uuid.uuid4())]})
    assert resp.status_code == 404
