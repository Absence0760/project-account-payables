"""Goods-receipt router tests — backend/app/api/goods_receipts.py.

Real-Postgres harness (`realdb`): two persistent test tenants `a` / `b`.

Covers:
  - list: empty; pagination shape (items + total); po_id + status filters;
    PO-number join for linked receipts; ordering; line_count
  - detail: happy path with line items + PO number; 404 for missing/cross-tenant;
    422 for a malformed UUID path param
  - auth: every endpoint is behind get_current_user (401 without a token)
  - tenant isolation: receipts inserted under `a` are invisible to `b`
"""

from __future__ import annotations

import uuid
from datetime import date

from app.models.procurement import GoodsReceipt, GRLineItem, PurchaseOrder


async def _add_po(realdb, key: str, **kwargs) -> uuid.UUID:
    from decimal import Decimal

    mk = realdb.sessionmaker(key)
    kwargs.setdefault("total", Decimal("100.00"))
    async with mk() as s:
        po = PurchaseOrder(organization_id=realdb.info(key).org_id, **kwargs)
        s.add(po)
        await s.commit()
        return po.id


async def _add_gr(realdb, key: str, *, lines=(), **kwargs) -> uuid.UUID:
    mk = realdb.sessionmaker(key)
    async with mk() as s:
        gr = GoodsReceipt(organization_id=realdb.info(key).org_id, **kwargs)
        s.add(gr)
        await s.flush()
        for ln in lines:
            s.add(GRLineItem(gr_id=gr.id, **ln))
        await s.commit()
        return gr.id


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def test_list_empty(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/goods-receipts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == [] and body["total"] == 0


async def test_list_returns_items_and_total(realdb):
    await _add_gr(
        realdb,
        "a",
        gr_number="GR-001",
        status="received",
        received_date=date(2026, 1, 5),
        lines=[{"description": "Widgets"}, {"description": "Gadgets"}],
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/goods-receipts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["gr_number"] == "GR-001"
    assert item["status"] == "received"
    assert item["line_count"] == 2
    assert item["received_date"] == "2026-01-05"
    assert item["po_id"] is None
    assert item["po_number"] is None


async def test_list_joins_po_number(realdb):
    po_id = await _add_po(realdb, "a", po_number="PO-2026-009")
    await _add_gr(realdb, "a", gr_number="GR-009", po_id=po_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/goods-receipts")
    item = resp.json()["items"][0]
    assert item["po_id"] == str(po_id)
    assert item["po_number"] == "PO-2026-009"


async def test_list_filter_by_po_id(realdb):
    po1 = await _add_po(realdb, "a", po_number="PO-1")
    po2 = await _add_po(realdb, "a", po_number="PO-2")
    await _add_gr(realdb, "a", gr_number="GR-A", po_id=po1)
    await _add_gr(realdb, "a", gr_number="GR-B", po_id=po2)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/goods-receipts", params={"po_id": str(po1)})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["gr_number"] == "GR-A"


async def test_list_filter_by_status(realdb):
    await _add_gr(realdb, "a", gr_number="GR-OPEN", status="received")
    await _add_gr(realdb, "a", gr_number="GR-DONE", status="matched")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/goods-receipts", params={"status": "matched"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["gr_number"] == "GR-DONE"


async def test_list_orders_by_received_date_desc(realdb):
    await _add_gr(realdb, "a", gr_number="GR-OLD", received_date=date(2026, 1, 1))
    await _add_gr(realdb, "a", gr_number="GR-NEW", received_date=date(2026, 6, 1))

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/goods-receipts")
    nums = [i["gr_number"] for i in resp.json()["items"]]
    assert nums == ["GR-NEW", "GR-OLD"]


async def test_list_pagination(realdb):
    for i in range(3):
        await _add_gr(realdb, "a", gr_number=f"GR-{i}", received_date=date(2026, 1, i + 1))

    async with realdb.client(key="a", role="ap_manager") as c:
        page1 = await c.get("/api/goods-receipts", params={"page": 1, "page_size": 2})
        page2 = await c.get("/api/goods-receipts", params={"page": 2, "page_size": 2})

    assert page1.json()["total"] == 3
    assert len(page1.json()["items"]) == 2
    assert len(page2.json()["items"]) == 1


async def test_list_page_size_validation(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/goods-receipts", params={"page_size": 0})
    assert resp.status_code == 422


async def test_list_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/goods-receipts")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# detail
# ---------------------------------------------------------------------------


async def test_get_detail_happy_path(realdb):
    po_id = await _add_po(realdb, "a", po_number="PO-DET-1")
    gr_id = await _add_gr(
        realdb,
        "a",
        gr_number="GR-DET-1",
        po_id=po_id,
        status="received",
        received_date=date(2026, 3, 3),
        lines=[
            {"description": "Bolts", "quantity_received": 12},
            {"description": "Nuts"},
        ],
    )

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/goods-receipts/{gr_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(gr_id)
    assert body["gr_number"] == "GR-DET-1"
    assert body["po_id"] == str(po_id)
    assert body["po_number"] == "PO-DET-1"
    assert body["received_date"] == "2026-03-03"
    assert len(body["line_items"]) == 2
    descs = {li["description"] for li in body["line_items"]}
    assert descs == {"Bolts", "Nuts"}
    bolts = next(li for li in body["line_items"] if li["description"] == "Bolts")
    assert bolts["quantity_received"] == 12.0
    nuts = next(li for li in body["line_items"] if li["description"] == "Nuts")
    assert nuts["quantity_received"] is None


async def test_get_detail_without_po(realdb):
    gr_id = await _add_gr(realdb, "a", gr_number="GR-NOPO")
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/goods-receipts/{gr_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["po_id"] is None
    assert body["po_number"] is None
    assert body["line_items"] == []


async def test_get_detail_404_for_missing(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/goods-receipts/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Goods receipt not found"


async def test_get_detail_422_for_bad_uuid(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/goods-receipts/not-a-uuid")
    assert resp.status_code == 422


async def test_get_detail_requires_auth(realdb):
    gr_id = await _add_gr(realdb, "a", gr_number="GR-AUTH")
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get(f"/api/goods-receipts/{gr_id}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# tenant isolation
# ---------------------------------------------------------------------------


async def test_tenant_isolation_list(realdb):
    await _add_gr(realdb, "a", gr_number="GR-A-ONLY")

    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get("/api/goods-receipts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == [] and body["total"] == 0


async def test_tenant_isolation_detail_404(realdb):
    gr_id = await _add_gr(realdb, "a", gr_number="GR-CROSS")

    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get(f"/api/goods-receipts/{gr_id}")
    assert resp.status_code == 404
