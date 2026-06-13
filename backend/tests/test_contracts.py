"""Real-DB coverage for the contracts router.

Covers ``backend/app/api/contracts.py`` end-to-end against the live test
tenants: repository CRUD, document upload, the lifecycle transitions
(activate / terminate / cancel / renew) with their 409 guards, the
spend-to-contract rollup (``services.contract_spend``), RBAC, tenant
isolation, audit rows, and exact ``Numeric`` money round-trips.
"""

from decimal import Decimal

from sqlalchemy import func, select

from app.models.contract import Contract
from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor import Vendor
from app.models.workflow import AuditLog


async def _add_vendor(mk, org_id, name="Globex Industrial") -> str:
    async with mk() as s:
        v = Vendor(organization_id=org_id, name=name)
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return str(v.id)


async def _create_contract(c, vendor_id, **overrides) -> dict:
    body = {
        "contract_number": "MSA-2026-001",
        "title": "Master Services Agreement",
        "contract_type": "msa",
        "vendor_id": vendor_id,
        "currency": "USD",
        "total_value": "120000.00",
        "spend_limit": "100000.00",
        "end_date": "2027-01-01",
        "renewal_notice_days": 45,
    }
    body.update(overrides)
    resp = await c.post("/api/contracts", json=body)
    return resp


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def test_create_contract_with_line_items(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await _create_contract(
            c,
            vendor_id,
            line_items=[
                {
                    "description": "Tier 1 support",
                    "quantity": "12",
                    "unit_price": "1000.00",
                    "total": "12000.00",
                    "gl_account": "6000",
                },
            ],
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["contract_number"] == "MSA-2026-001"
    assert body["status"] == "draft"
    assert body["contract_type"] == "msa"
    assert body["vendor_name"] == "Globex Industrial"
    assert len(body["line_items"]) == 1
    assert body["line_items"][0]["line_number"] == 1

    # Exact Decimal round-trip through Numeric(15, 2).
    async with mk() as s:
        contract = (await s.execute(select(Contract))).scalar_one()
        assert contract.total_value == Decimal("120000.00")
        assert contract.spend_limit == Decimal("100000.00")
        assert contract.organization_id == org_id
        # Audit row written on create.
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.entity_type == "contract")))
            .scalars()
            .all()
        )
        assert "contract.created" in actions


async def test_create_contract_unknown_vendor_404(realdb):
    import uuid

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await _create_contract(c, str(uuid.uuid4()))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# list + filters
# ---------------------------------------------------------------------------


async def test_list_and_filter(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        await _create_contract(c, vendor_id, contract_number="C-AAA", contract_type="msa")
        await _create_contract(c, vendor_id, contract_number="C-BBB", contract_type="lease")

    # A clerk can read the repository even though it can't create.
    async with realdb.client(key="a", role="ap_clerk") as c:
        all_resp = await c.get("/api/contracts")
        assert all_resp.status_code == 200
        assert all_resp.json()["total"] >= 2

        typed = await c.get("/api/contracts?contract_type=lease")
        assert all(i["contract_type"] == "lease" for i in typed.json()["items"])

        searched = await c.get("/api/contracts?search=C-AAA")
        nums = [i["contract_number"] for i in searched.json()["items"]]
        assert "C-AAA" in nums and "C-BBB" not in nums


# ---------------------------------------------------------------------------
# spend rollup
# ---------------------------------------------------------------------------


async def test_spend_summary_over_limit(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        created = (await _create_contract(c, vendor_id, spend_limit="1000.00")).json()
        contract_id = created["id"]

    # Link two invoices directly (the link endpoint lands in a later slice).
    import uuid

    async with mk() as s:
        for amt in (Decimal("600.00"), Decimal("700.00")):
            s.add(
                Invoice(
                    organization_id=org_id,
                    invoice_number=f"INV-{uuid.uuid4().hex[:6]}",
                    vendor_name="Globex Industrial",
                    amount=amt,
                    status=InvoiceStatus.approved,
                    vendor_id=uuid.UUID(vendor_id),
                    contract_id=uuid.UUID(contract_id),
                )
            )
        # A rejected invoice must NOT count toward spend.
        s.add(
            Invoice(
                organization_id=org_id,
                invoice_number=f"INV-{uuid.uuid4().hex[:6]}",
                vendor_name="Globex Industrial",
                amount=Decimal("9999.00"),
                status=InvoiceStatus.rejected,
                vendor_id=uuid.UUID(vendor_id),
                contract_id=uuid.UUID(contract_id),
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        detail = (await c.get(f"/api/contracts/{contract_id}")).json()
    assert detail["spend"]["invoiced_total"] == 1300.0
    assert detail["spend"]["invoice_count"] == 2
    assert detail["spend"]["remaining"] == -300.0
    assert detail["spend"]["over_limit"] is True


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


async def test_update_contract(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        contract_id = (await _create_contract(c, vendor_id)).json()["id"]
        resp = await c.patch(
            f"/api/contracts/{contract_id}",
            json={"title": "Renamed MSA", "spend_limit": "150000.00"},
        )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed MSA"
    assert resp.json()["spend_limit"] == 150000.0

    async with mk() as s:
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.action == "contract.updated")))
            .scalars()
            .all()
        )
        assert len(actions) >= 1


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


async def test_lifecycle_activate_terminate(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        contract_id = (await _create_contract(c, vendor_id)).json()["id"]

        activated = await c.post(f"/api/contracts/{contract_id}/activate")
        assert activated.status_code == 200
        assert activated.json()["status"] == "active"

        # Can't activate an already-active contract.
        again = await c.post(f"/api/contracts/{contract_id}/activate")
        assert again.status_code == 409

        terminated = await c.post(f"/api/contracts/{contract_id}/terminate")
        assert terminated.status_code == 200
        assert terminated.json()["status"] == "terminated"


async def test_renew_extends_and_clears_alert(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        contract_id = (await _create_contract(c, vendor_id, end_date="2027-01-01")).json()["id"]
        await c.post(f"/api/contracts/{contract_id}/activate")

        # Renewing to an earlier date is rejected.
        bad = await c.post(f"/api/contracts/{contract_id}/renew", json={"end_date": "2026-06-01"})
        assert bad.status_code == 400

        renewed = await c.post(
            f"/api/contracts/{contract_id}/renew",
            json={"end_date": "2028-01-01", "spend_limit": "200000.00"},
        )
    assert renewed.status_code == 200
    assert renewed.json()["end_date"] == "2028-01-01"
    assert renewed.json()["status"] == "active"
    assert renewed.json()["renewal_alert_sent_at"] is None


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_guarded_by_status(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        contract_id = (await _create_contract(c, vendor_id)).json()["id"]
        await c.post(f"/api/contracts/{contract_id}/activate")

        # Active contract cannot be deleted.
        blocked = await c.delete(f"/api/contracts/{contract_id}")
        assert blocked.status_code == 409

        await c.post(f"/api/contracts/{contract_id}/cancel")
        ok = await c.delete(f"/api/contracts/{contract_id}")
        assert ok.status_code == 204


# ---------------------------------------------------------------------------
# RBAC + tenant isolation
# ---------------------------------------------------------------------------


async def test_clerk_cannot_create(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await _create_contract(c, vendor_id)
    assert resp.status_code == 403


async def test_tenant_isolation(realdb):
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    vendor_a = await _add_vendor(mk_a, org_a)

    async with realdb.client(key="a", role="ap_manager") as c:
        contract_id = (await _create_contract(c, vendor_a, contract_number="ISO-A")).json()["id"]

    # Tenant B must not see tenant A's contract.
    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get(f"/api/contracts/{contract_id}")
        assert resp.status_code == 404
        listing = await c.get("/api/contracts?search=ISO-A")
        assert listing.json()["total"] == 0


# ---------------------------------------------------------------------------
# document upload
# ---------------------------------------------------------------------------


async def test_upload_document(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        contract_id = (await _create_contract(c, vendor_id)).json()["id"]
        resp = await c.post(
            f"/api/contracts/{contract_id}/upload",
            files={"file": ("contract.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["file_key"].startswith(f"{org_id}/contracts/{contract_id}/")
    assert body["file_url"].startswith("/api/contracts/file/")

    async with mk() as s:
        actions = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "contract.document_uploaded")
            )
        ).scalar()
        assert actions >= 1


# ---------------------------------------------------------------------------
# spend-to-contract linking (invoices router)
# ---------------------------------------------------------------------------


async def _add_invoice(mk, org_id, vendor_id, *, amount="500.00") -> str:
    import uuid

    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            invoice_number=f"INV-{uuid.uuid4().hex[:6]}",
            vendor_name="Globex Industrial",
            amount=Decimal(amount),
            status=InvoiceStatus.approved,
            vendor_id=uuid.UUID(vendor_id),
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return str(inv.id)


async def test_link_and_unlink_contract(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        contract_id = (await _create_contract(c, vendor_id, spend_limit="1000.00")).json()["id"]
        invoice_id = await _add_invoice(mk, org_id, vendor_id, amount="400.00")

        linked = await c.post(
            f"/api/invoices/{invoice_id}/link-contract", json={"contract_id": contract_id}
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["contract_id"] == contract_id

        # Spend rollup now reflects the linked invoice.
        detail = (await c.get(f"/api/contracts/{contract_id}")).json()
        assert detail["spend"]["invoiced_total"] == 400.0
        assert detail["spend"]["invoice_count"] == 1

        unlinked = await c.post(f"/api/invoices/{invoice_id}/unlink-contract")
        assert unlinked.status_code == 200
        assert unlinked.json()["contract_id"] is None

        detail2 = (await c.get(f"/api/contracts/{contract_id}")).json()
        assert detail2["spend"]["invoice_count"] == 0

    async with mk() as s:
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(
                        AuditLog.action.in_(
                            ["invoice.contract_linked", "invoice.contract_unlinked"]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "invoice.contract_linked" in actions
        assert "invoice.contract_unlinked" in actions


async def test_link_unknown_contract_404(realdb):
    import uuid

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    invoice_id = await _add_invoice(mk, org_id, vendor_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/invoices/{invoice_id}/link-contract",
            json={"contract_id": str(uuid.uuid4())},
        )
    assert resp.status_code == 404
