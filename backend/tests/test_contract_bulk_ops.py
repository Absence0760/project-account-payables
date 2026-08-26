"""Real-DB coverage for the contract bulk-operation endpoints.

`/api/contracts/ids` (the "select all N matching" resolver), `/bulk/status`
(bulk activate/terminate/cancel), and `/bulk/export` (CSV) — the power-user
volume affordances `/contracts` shipped without despite being one of the
five primary list pages. `/bulk/status` routes each contract through the
SAME `_transition` helper the single-row lifecycle endpoints use, so it
can't reach a state (or skip an audit row) they wouldn't — and shares the
skip-and-report partial-success contract with the sibling bulk endpoints.
"""

import uuid

from sqlalchemy import select

from app.models.contract import Contract
from app.models.vendor import Vendor
from app.models.workflow import AuditLog

TENANT = "a"


async def _add_vendor(mk, org_id, name="Bulk Contract Vendor") -> str:
    async with mk() as s:
        v = Vendor(organization_id=org_id, name=name)
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return str(v.id)


async def _add_contract(mk, org_id, vendor_id, *, number, status="draft") -> str:
    async with mk() as s:
        c = Contract(
            organization_id=org_id,
            contract_number=number,
            vendor_id=uuid.UUID(vendor_id),
            status=status,
            currency="USD",
        )
        s.add(c)
        await s.commit()
        await s.refresh(c)
        return str(c.id)


# ---------------------------------------------------------------------------
# GET /contracts/ids
# ---------------------------------------------------------------------------


async def test_contract_ids_matches_list_filters(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    vendor_id = await _add_vendor(mk, org_id)
    matching = [await _add_contract(mk, org_id, vendor_id, number=f"MSA-IDS-{i}") for i in range(3)]
    other_vendor = await _add_vendor(mk, org_id, name="Other Vendor")
    await _add_contract(mk, org_id, other_vendor, number="MSA-OTHER-1")

    async with realdb.client(key=TENANT) as c:
        resp = await c.get("/api/contracts/ids", params={"vendor_id": vendor_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert set(body["ids"]) == set(matching)


async def test_contract_ids_exceeds_single_page(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    vendor_id = await _add_vendor(mk, org_id)
    created = [await _add_contract(mk, org_id, vendor_id, number=f"MSA-VOL-{i}") for i in range(25)]

    async with realdb.client(key=TENANT) as c:
        resp = await c.get("/api/contracts/ids", params={"vendor_id": vendor_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 25
    assert body["truncated"] is False
    assert set(body["ids"]) == set(created)


# ---------------------------------------------------------------------------
# POST /contracts/bulk/status
# ---------------------------------------------------------------------------


async def test_bulk_activate_updates_and_audits(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    actor_id = realdb.info(TENANT).users["admin"]
    vendor_id = await _add_vendor(mk, org_id)
    ids = [await _add_contract(mk, org_id, vendor_id, number=f"MSA-ACT-{i}") for i in range(3)]

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post("/api/contracts/bulk/status", json={"ids": ids, "action": "activate"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 3
    assert body["skipped"] == []

    async with mk() as s:
        rows = (await s.execute(select(Contract).where(Contract.id.in_(ids)))).scalars().all()
        assert all(str(c.status) == "active" for c in rows)
        audit_rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "contract.active",
                        AuditLog.entity_id.in_(ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_rows) == 3
        assert all(r.actor_id == actor_id for r in audit_rows)


async def test_bulk_status_skips_illegal_and_missing_rows_not_the_whole_batch(realdb):
    """Same partial-success contract as the invoice/expense/vendor bulk
    endpoints: an already-active contract can't `activate` again (mirrors
    the single-row endpoint's 409), a stale id doesn't resolve — neither
    should stop the other, valid, row in the SAME batch from landing."""
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    vendor_id = await _add_vendor(mk, org_id)
    good_id = await _add_contract(mk, org_id, vendor_id, number="MSA-GOOD-1", status="draft")
    already_active_id = await _add_contract(
        mk, org_id, vendor_id, number="MSA-ALREADY-1", status="active"
    )
    missing_id = str(uuid.uuid4())

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(
            "/api/contracts/bulk/status",
            json={"ids": [good_id, already_active_id, missing_id], "action": "activate"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 1
    skipped_ids = {s["id"] for s in body["skipped"]}
    assert skipped_ids == {already_active_id, missing_id}

    async with mk() as s:
        good = (await s.execute(select(Contract).where(Contract.id == good_id))).scalar_one()
        assert str(good.status) == "active"


async def test_bulk_status_rejects_out_of_scope_action(realdb):
    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(
            "/api/contracts/bulk/status", json={"ids": [str(uuid.uuid4())], "action": "renew"}
        )
    # `renew` isn't in `ContractBulkAction` (it needs a per-contract
    # end_date and isn't a bulk fit) — refused by Pydantic before the
    # endpoint runs.
    assert resp.status_code == 422, resp.text


async def test_bulk_status_requires_manager_role(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    vendor_id = await _add_vendor(mk, org_id)
    cid = await _add_contract(mk, org_id, vendor_id, number="MSA-RBAC-1")

    async with realdb.client(key=TENANT, role="ap_clerk") as c:
        resp = await c.post("/api/contracts/bulk/status", json={"ids": [cid], "action": "activate"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /contracts/bulk/export
# ---------------------------------------------------------------------------


async def test_bulk_export_returns_csv(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    vendor_id = await _add_vendor(mk, org_id, name="CSV Export Vendor")
    ids = [await _add_contract(mk, org_id, vendor_id, number=f"MSA-CSV-{i}") for i in range(2)]

    async with realdb.client(key=TENANT) as c:
        resp = await c.post("/api/contracts/bulk/export", json={"ids": ids})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.text
    assert "MSA-CSV-0" in text
    assert "MSA-CSV-1" in text
    assert "CSV Export Vendor" in text


async def test_bulk_export_unknown_ids_404(realdb):
    async with realdb.client(key=TENANT) as c:
        resp = await c.post("/api/contracts/bulk/export", json={"ids": [str(uuid.uuid4())]})
    assert resp.status_code == 404
