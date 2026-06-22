"""Real-Postgres coverage for the purchase-orders router.

Covers list / detail / sync-erp:
- list happy path + search / status / vendor filters + pagination shape
- detail with line items, vendor name, and linked invoices (matched by po_number)
- detail 404 for an unknown id
- RBAC on sync-erp (admin/ap_manager only) + the "no ERP configured" 400
- sync-erp against the mock adapter: creates POs, links vendors, is idempotent
- tenant isolation: rows under tenant A are invisible to tenant B

The list/detail endpoints are auth-gated but role-open (any authenticated
user). sync-erp is gated to ROLE_ADMIN / ROLE_AP_MANAGER via require_roles.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings as cfg
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.procurement import POLineItem, PurchaseOrder
from app.models.vendor import Vendor

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _set_org_erp(org_id: uuid.UUID, erp_config: dict | None) -> None:
    """Patch the control-plane Organization.settings.erp for one org.

    sync-erp reads ``org.settings["erp"]`` off the control DB row, so the
    realdb tenant sessionmaker can't reach it — go straight to the control
    engine via the configured database_url.
    """
    engine = create_async_engine(cfg.database_url)
    mk = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with mk() as s:
            org = (
                await s.execute(select(Organization).where(Organization.id == org_id))
            ).scalar_one()
            settings = dict(org.settings or {})
            if erp_config is None:
                settings.pop("erp", None)
            else:
                settings["erp"] = erp_config
            org.settings = settings
            await s.commit()
    finally:
        await engine.dispose()


async def _add_po(
    mk,
    org_id: uuid.UUID,
    *,
    po_number: str,
    total: Decimal,
    status: str = "open",
    vendor_id: uuid.UUID | None = None,
    line_items: list[dict] | None = None,
) -> uuid.UUID:
    async with mk() as s:
        po = PurchaseOrder(
            po_number=po_number,
            total=total,
            status=status,
            vendor_id=vendor_id,
            organization_id=org_id,
        )
        s.add(po)
        await s.flush()
        for li in line_items or []:
            s.add(POLineItem(po_id=po.id, **li))
        await s.commit()
        return po.id


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def test_list_purchase_orders_happy_path(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_po(
        mk,
        org_id,
        po_number="PO-1",
        total=Decimal("1200.50"),
        line_items=[
            {
                "description": "Widgets",
                "quantity": Decimal("10"),
                "unit_price": Decimal("120.05"),
                "total": Decimal("1200.50"),
            }
        ],
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/purchase-orders")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["po_number"] == "PO-1"
    assert item["total"] == 1200.50
    assert item["status"] == "open"
    assert len(item["line_items"]) == 1
    assert item["line_items"][0]["description"] == "Widgets"


async def test_list_purchase_orders_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/purchase-orders")
    assert resp.status_code == 401


async def test_list_purchase_orders_search_and_status_filter(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_po(mk, org_id, po_number="PO-ALPHA", total=Decimal("100"), status="open")
    await _add_po(mk, org_id, po_number="PO-BETA", total=Decimal("200"), status="closed")

    async with realdb.client(key="a", role="ap_manager") as c:
        # search matches po_number ilike
        resp = await c.get("/api/purchase-orders", params={"search": "alpha"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["po_number"] == "PO-ALPHA"

        # status filter (alias "status")
        resp = await c.get("/api/purchase-orders", params={"status": "closed"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["po_number"] == "PO-BETA"


async def test_list_purchase_orders_vendor_filter_and_name(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    async with mk() as s:
        vendor = Vendor(name="Office Supplies Co", organization_id=org_id)
        s.add(vendor)
        await s.flush()
        vendor_id = vendor.id
        await s.commit()

    await _add_po(mk, org_id, po_number="PO-V", total=Decimal("500"), vendor_id=vendor_id)
    await _add_po(mk, org_id, po_number="PO-NOV", total=Decimal("600"))

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/purchase-orders", params={"vendor_id": str(vendor_id)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["po_number"] == "PO-V"
    assert item["vendor_id"] == str(vendor_id)
    assert item["vendor_name"] == "Office Supplies Co"


async def test_list_purchase_orders_pagination(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    for i in range(5):
        await _add_po(mk, org_id, po_number=f"PO-{i}", total=Decimal("10"))

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/purchase-orders", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5  # total is the unpaged count
    assert len(body["items"]) == 2


async def test_list_purchase_orders_tenant_isolation(realdb):
    mk_a = realdb.sessionmaker("a")
    await _add_po(mk_a, realdb.info("a").org_id, po_number="PO-SECRET", total=Decimal("999"))

    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get("/api/purchase-orders")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# detail
# ---------------------------------------------------------------------------


async def test_get_purchase_order_detail_with_invoices(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    async with mk() as s:
        vendor = Vendor(name="Cloud Services Inc", organization_id=org_id)
        s.add(vendor)
        await s.flush()
        vendor_id = vendor.id
        await s.commit()

    po_id = await _add_po(
        mk,
        org_id,
        po_number="PO-2024-201",
        total=Decimal("15000.00"),
        vendor_id=vendor_id,
        line_items=[
            {
                "description": "SaaS license",
                "quantity": Decimal("1"),
                "unit_price": Decimal("12000.00"),
                "total": Decimal("12000.00"),
            }
        ],
    )

    # An invoice that references the PO number is surfaced under linked_invoices.
    async with mk() as s:
        s.add(
            Invoice(
                invoice_number="INV-1",
                vendor_name="Cloud Services Inc",
                amount=Decimal("12000.00"),
                po_number="PO-2024-201",
                status=InvoiceStatus.approved,
                organization_id=org_id,
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/purchase-orders/{po_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["po_number"] == "PO-2024-201"
    assert body["vendor_name"] == "Cloud Services Inc"
    assert body["total"] == 15000.00
    assert len(body["line_items"]) == 1
    assert len(body["linked_invoices"]) == 1
    assert body["linked_invoices"][0]["invoice_number"] == "INV-1"
    assert body["linked_invoices"][0]["status"] == "approved"


async def test_get_purchase_order_404(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/purchase-orders/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_get_purchase_order_tenant_isolation(realdb):
    mk_a = realdb.sessionmaker("a")
    po_id = await _add_po(mk_a, realdb.info("a").org_id, po_number="PO-X", total=Decimal("5"))

    # Tenant B's DB has no such row → 404, not a leak.
    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get(f"/api/purchase-orders/{po_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# sync-erp
# ---------------------------------------------------------------------------


async def test_sync_erp_no_config_returns_400(realdb):
    await _set_org_erp(realdb.info("a").org_id, None)
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/purchase-orders/sync-erp")
    assert resp.status_code == 400
    assert "No ERP configured" in resp.json()["detail"]


async def test_sync_erp_rbac_forbidden_roles(realdb):
    # Even with ERP configured, ap_clerk and cfo are not permitted.
    await _set_org_erp(realdb.info("a").org_id, {"type": "mock", "integration_method": "direct"})
    for role in ("ap_clerk", "cfo"):
        async with realdb.client(key="a", role=role) as c:
            resp = await c.post("/api/purchase-orders/sync-erp")
        assert resp.status_code == 403, role


async def test_sync_erp_creates_pos_and_is_idempotent(realdb):
    org_id = realdb.info("a").org_id
    await _set_org_erp(org_id, {"type": "mock", "integration_method": "direct"})

    # Seed one vendor whose name matches a mock PO so vendor linking exercises.
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        s.add(Vendor(name="Office Supplies Co", organization_id=org_id))
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/purchase-orders/sync-erp")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["created"] >= 1
        assert body["skipped"] == 0
        assert body["adapter"] == "mock"
        created_first = body["created"]

        # Second sync: every PO already exists → all skipped, none created.
        resp2 = await c.post("/api/purchase-orders/sync-erp")
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["created"] == 0
        assert body2["skipped"] == created_first

    # The matching-name PO got its vendor_id linked.
    async with mk() as s:
        po_count = (await s.execute(select(func.count()).select_from(PurchaseOrder))).scalar_one()
        assert po_count == created_first
        linked = (
            await s.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2024-200"))
        ).scalar_one_or_none()
        assert linked is not None
        assert linked.vendor_id is not None


async def test_sync_erp_isolated_per_tenant(realdb):
    """A sync against tenant A leaves tenant B's PO table empty."""
    await _set_org_erp(realdb.info("a").org_id, {"type": "mock", "integration_method": "direct"})
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/purchase-orders/sync-erp")
    assert resp.status_code == 200
    assert resp.json()["created"] >= 1

    mk_b = realdb.sessionmaker("b")
    async with mk_b() as s:
        count_b = (await s.execute(select(func.count()).select_from(PurchaseOrder))).scalar_one()
    assert count_b == 0


# ---------------------------------------------------------------------------
# sync-erp — expected_delivery_date auto-population (migration 0060 signal)
# ---------------------------------------------------------------------------


async def test_sync_erp_populates_expected_delivery_date_on_create(realdb):
    """The mock catalogue carries deterministic promised dates on two of its
    three POs; the sync mapper must persist them onto the new rows (and leave
    the third, which has no ERP date, NULL)."""
    from datetime import date

    org_id = realdb.info("a").org_id
    await _set_org_erp(org_id, {"type": "mock", "integration_method": "direct"})

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/purchase-orders/sync-erp")
    assert resp.status_code == 200

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        rows = (await s.execute(select(PurchaseOrder))).scalars().all()
        by_number = {p.po_number: p for p in rows}
    assert by_number["PO-2024-200"].expected_delivery_date == date(2024, 6, 15)
    assert by_number["PO-2024-201"].expected_delivery_date == date(2024, 7, 1)
    # The ERP supplied no date for this one → stays NULL, never fabricated.
    assert by_number["PO-2024-202"].expected_delivery_date is None


async def test_sync_erp_does_not_clobber_human_set_expected_delivery_date(realdb):
    """A date already on the row (set via the model/API) wins — a re-sync must
    never overwrite it, even though the ERP supplies its own date for that PO."""
    from datetime import date

    org_id = realdb.info("a").org_id
    await _set_org_erp(org_id, {"type": "mock", "integration_method": "direct"})

    # Pre-seed PO-2024-200 with a human-chosen expected date that differs from
    # the mock ERP's (2024-06-15).
    human_date = date(2030, 1, 1)
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        po = PurchaseOrder(
            po_number="PO-2024-200",
            total=Decimal("2500.00"),
            status="open",
            expected_delivery_date=human_date,
            organization_id=org_id,
        )
        s.add(po)
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/purchase-orders/sync-erp")
    assert resp.status_code == 200

    async with mk() as s:
        kept = (
            await s.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2024-200"))
        ).scalar_one()
    # Human value preserved — the sync neither overwrote it nor created a dup.
    assert kept.expected_delivery_date == human_date


async def test_sync_erp_backfills_missing_expected_delivery_date_on_existing_po(realdb):
    """An existing PO with no expected date gets the ERP's promised date filled
    in on a re-sync (the auto-population back-fill), reported as ``updated``."""
    from datetime import date

    org_id = realdb.info("a").org_id
    await _set_org_erp(org_id, {"type": "mock", "integration_method": "direct"})

    # Pre-seed PO-2024-201 WITHOUT an expected date (e.g. created before the
    # ERP started supplying one); the mock ERP carries 2024-07-01 for it.
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        po = PurchaseOrder(
            po_number="PO-2024-201",
            total=Decimal("15000.00"),
            status="open",
            organization_id=org_id,
        )
        s.add(po)
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/purchase-orders/sync-erp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] >= 1

    async with mk() as s:
        filled = (
            await s.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2024-201"))
        ).scalar_one()
    assert filled.expected_delivery_date == date(2024, 7, 1)
