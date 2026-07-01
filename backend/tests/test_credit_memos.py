"""Real-DB coverage for the credit-memos router.

Covers ``backend/app/api/credit_memos.py`` end-to-end against two live test
tenants: list/get, create (open + applied-at-creation), apply-to-invoice,
void, the 409 lifecycle guards, RBAC, tenant isolation, and the Decimal money
math (amounts are ``Numeric(15, 2)`` and must round-trip exactly).
"""

from decimal import Decimal

from sqlalchemy import func, select

from app.models.credit_memo import CreditMemo
from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor import Vendor


async def _add_vendor(mk, org_id, name="Acme Supplies") -> str:
    async with mk() as s:
        v = Vendor(organization_id=org_id, name=name)
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return str(v.id)


async def _add_invoice(mk, org_id, *, vendor_id=None, number="INV-1") -> str:
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            invoice_number=number,
            vendor_name="Acme Supplies",
            amount=Decimal("500.00"),
            status=InvoiceStatus.new,
            vendor_id=vendor_id,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return str(inv.id)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def test_create_open_memo(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-001",
                "vendor_id": vendor_id,
                "amount": "123.45",
                "currency": "USD",
                "reason": "Returned goods",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["memo_number"] == "CM-001"
    assert body["vendor_id"] == vendor_id
    assert body["vendor_name"] == "Acme Supplies"
    # Open memo — no invoice link, no application metadata yet.
    assert body["status"] == "open"
    assert body["invoice_id"] is None
    assert body["applied_at"] is None
    assert body["applied_by"] is None
    # Decimal round-trips exactly through Numeric(15, 2).
    assert body["amount"] == 123.45

    # Persisted amount is an exact Decimal, not a lossy float.
    async with mk() as s:
        memo = (await s.execute(select(CreditMemo))).scalar_one()
        assert memo.amount == Decimal("123.45")
        assert memo.status == "open"
        assert memo.organization_id == org_id


async def test_create_applied_at_creation_with_invoice(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, number="INV-100")

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-002",
                "vendor_id": vendor_id,
                "amount": "50.00",
                "invoice_id": invoice_id,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Linking an invoice at creation flips the memo straight to 'applied'.
    assert body["status"] == "applied"
    assert body["invoice_id"] == invoice_id
    assert body["invoice_number"] == "INV-100"
    assert body["applied_at"] is not None
    assert body["applied_by"] == "admin"  # seeded user's full_name == role name


async def test_create_unknown_vendor_404(realdb):
    import uuid

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-X",
                "vendor_id": str(uuid.uuid4()),
                "amount": "10.00",
            },
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Vendor not found"


async def test_create_unknown_invoice_404(realdb):
    import uuid

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-Y",
                "vendor_id": vendor_id,
                "amount": "10.00",
                "invoice_id": str(uuid.uuid4()),
            },
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Invoice not found"


async def test_create_missing_required_field_422(realdb):
    # vendor_id omitted.
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/credit-memos",
            json={"memo_number": "CM-Z", "amount": "10.00"},
        )
    assert resp.status_code == 422


async def test_create_non_positive_amount_422(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    # amount has Field(gt=0): zero and negative must be rejected before any DB hit.
    async with realdb.client(key="a", role="ap_manager") as c:
        for bad in ("0", "-5.00"):
            resp = await c.post(
                "/api/credit-memos",
                json={"memo_number": "CM-NEG", "vendor_id": vendor_id, "amount": bad},
            )
            assert resp.status_code == 422, bad


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def test_list_empty(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/credit-memos")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == [] and body["total"] == 0


async def test_list_returns_memos_with_join_fields(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id, name="Globex")
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, number="INV-LIST")

    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(
            "/api/credit-memos",
            json={"memo_number": "CM-L1", "vendor_id": vendor_id, "amount": "10.00"},
        )
        await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-L2",
                "vendor_id": vendor_id,
                "amount": "20.00",
                "invoice_id": invoice_id,
            },
        )
        resp = await c.get("/api/credit-memos")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    # The outer join exposes vendor_name on every row, invoice_number when linked.
    by_number = {m["memo_number"]: m for m in body["items"]}
    assert by_number["CM-L1"]["vendor_name"] == "Globex"
    assert by_number["CM-L1"]["invoice_number"] is None
    assert by_number["CM-L2"]["invoice_number"] == "INV-LIST"


async def test_list_status_filter(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, number="INV-F")

    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(
            "/api/credit-memos",
            json={"memo_number": "CM-OPEN", "vendor_id": vendor_id, "amount": "10.00"},
        )
        await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-APPLIED",
                "vendor_id": vendor_id,
                "amount": "20.00",
                "invoice_id": invoice_id,
            },
        )
        open_resp = await c.get("/api/credit-memos", params={"status": "open"})
        applied_resp = await c.get("/api/credit-memos", params={"status": "applied"})

    assert open_resp.json()["total"] == 1
    assert open_resp.json()["items"][0]["memo_number"] == "CM-OPEN"
    assert applied_resp.json()["total"] == 1
    assert applied_resp.json()["items"][0]["memo_number"] == "CM-APPLIED"


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


async def _create_open_memo(c, vendor_id, *, number="CM-A", amount="100.00") -> str:
    resp = await c.post(
        "/api/credit-memos",
        json={"memo_number": number, "vendor_id": vendor_id, "amount": amount},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_apply_open_memo_to_invoice(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, number="INV-APPLY")

    async with realdb.client(key="a", role="ap_manager") as c:
        memo_id = await _create_open_memo(c, vendor_id)
        resp = await c.post(
            f"/api/credit-memos/{memo_id}/apply",
            json={"invoice_id": invoice_id},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "applied"
    assert body["invoice_id"] == invoice_id
    assert body["invoice_number"] == "INV-APPLY"
    assert body["applied_at"] is not None
    assert body["applied_by"] == "ap_manager"

    async with mk() as s:
        memo = (await s.execute(select(CreditMemo))).scalar_one()
        assert memo.status == "applied"
        assert str(memo.invoice_id) == invoice_id


async def test_apply_memo_not_found_404(realdb):
    import uuid

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    invoice_id = await _add_invoice(mk, org_id, number="INV-NF")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/credit-memos/{uuid.uuid4()}/apply",
            json={"invoice_id": invoice_id},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Credit memo not found"


async def test_apply_invoice_not_found_404(realdb):
    import uuid

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        memo_id = await _create_open_memo(c, vendor_id)
        resp = await c.post(
            f"/api/credit-memos/{memo_id}/apply",
            json={"invoice_id": str(uuid.uuid4())},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Invoice not found"


async def test_apply_already_applied_409(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, number="INV-APPLIED")

    async with realdb.client(key="a", role="ap_manager") as c:
        memo_id = await _create_open_memo(c, vendor_id)
        first = await c.post(f"/api/credit-memos/{memo_id}/apply", json={"invoice_id": invoice_id})
        assert first.status_code == 200
        # Re-applying an already-applied memo is a conflict, not a re-write.
        second = await c.post(f"/api/credit-memos/{memo_id}/apply", json={"invoice_id": invoice_id})
    assert second.status_code == 409
    assert "applied" in second.json()["detail"]


async def test_apply_vendor_mismatch_409(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    memo_vendor = await _add_vendor(mk, org_id, name="Memo Vendor")
    other_vendor = await _add_vendor(mk, org_id, name="Other Vendor")
    # Invoice belongs to a *different* vendor than the memo.
    invoice_id = await _add_invoice(mk, org_id, vendor_id=other_vendor, number="INV-MM")

    async with realdb.client(key="a", role="ap_manager") as c:
        memo_id = await _create_open_memo(c, memo_vendor)
        resp = await c.post(
            f"/api/credit-memos/{memo_id}/apply",
            json={"invoice_id": invoice_id},
        )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Credit memo vendor does not match invoice vendor"


async def test_apply_currency_mismatch_409(realdb):
    """A EUR memo can't be applied to a USD invoice — the remaining-balance math
    subtracts the amounts directly, so mixed currencies would corrupt it."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id, name="FX Vendor")
    # _add_invoice leaves currency at the USD default.
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, number="INV-FX")

    async with realdb.client(key="a", role="ap_manager") as c:
        memo = await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-EUR",
                "vendor_id": vendor_id,
                "amount": "50.00",
                "currency": "EUR",
            },
        )
        assert memo.status_code == 201, memo.text
        resp = await c.post(
            f"/api/credit-memos/{memo.json()['id']}/apply",
            json={"invoice_id": invoice_id},
        )
    assert resp.status_code == 409, resp.text
    assert "currency" in resp.json()["detail"].lower()


async def test_create_with_invoice_currency_mismatch_409(realdb):
    """Creating a memo directly against an invoice (invoice_id at create) applies
    it immediately — so the same currency guard as /apply must reject a EUR memo
    against a USD invoice, or the remaining-balance math mixes currencies."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id, name="FX Create Vendor")
    # _add_invoice leaves currency at the USD default.
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, number="INV-FXC")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-EURC",
                "vendor_id": vendor_id,
                "invoice_id": invoice_id,
                "amount": "50.00",
                "currency": "EUR",
            },
        )
    assert resp.status_code == 409, resp.text
    assert "currency" in resp.json()["detail"].lower()


async def test_apply_invoice_without_vendor_allowed(realdb):
    # When the invoice has no vendor_id, the vendor-match guard is skipped.
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    invoice_id = await _add_invoice(mk, org_id, vendor_id=None, number="INV-NOVEN")

    async with realdb.client(key="a", role="ap_manager") as c:
        memo_id = await _create_open_memo(c, vendor_id)
        resp = await c.post(
            f"/api/credit-memos/{memo_id}/apply",
            json={"invoice_id": invoice_id},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"


# ---------------------------------------------------------------------------
# void
# ---------------------------------------------------------------------------


async def test_void_open_memo(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        memo_id = await _create_open_memo(c, vendor_id)
        resp = await c.post(f"/api/credit-memos/{memo_id}/void")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "void"

    async with mk() as s:
        memo = (await s.execute(select(CreditMemo))).scalar_one()
        assert memo.status == "void"


async def test_void_applied_memo_409(realdb):
    # Applied credit memos are immutable for audit — voiding them is blocked.
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, number="INV-VA")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-VA",
                "vendor_id": vendor_id,
                "amount": "10.00",
                "invoice_id": invoice_id,
            },
        )
        memo_id = resp.json()["id"]
        void_resp = await c.post(f"/api/credit-memos/{memo_id}/void")
    assert void_resp.status_code == 409
    assert "Applied" in void_resp.json()["detail"]


async def test_void_not_found_404(realdb):
    import uuid

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/credit-memos/{uuid.uuid4()}/void")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Credit memo not found"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


async def test_list_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/credit-memos")
    assert resp.status_code == 401


async def test_create_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(
            "/api/credit-memos",
            json={"memo_number": "CM", "vendor_id": "x", "amount": "1.00"},
        )
    assert resp.status_code == 401


async def test_list_allows_cfo_and_clerk(realdb):
    # list permits admin/ap_manager/ap_clerk/cfo.
    for role in ("cfo", "ap_clerk"):
        async with realdb.client(key="a", role=role) as c:
            resp = await c.get("/api/credit-memos")
        assert resp.status_code == 200, role


async def test_create_forbidden_for_clerk(realdb):
    # create is admin/ap_manager only — ap_clerk is denied.
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/credit-memos",
            json={"memo_number": "CM-CLERK", "vendor_id": vendor_id, "amount": "10.00"},
        )
    assert resp.status_code == 403


async def test_create_forbidden_for_cfo(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post(
            "/api/credit-memos",
            json={"memo_number": "CM-CFO", "vendor_id": vendor_id, "amount": "10.00"},
        )
    assert resp.status_code == 403


async def test_apply_forbidden_for_clerk(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, number="INV-CLERK")
    async with realdb.client(key="a", role="ap_manager") as mgr:
        memo_id = await _create_open_memo(mgr, vendor_id)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            f"/api/credit-memos/{memo_id}/apply",
            json={"invoice_id": invoice_id},
        )
    assert resp.status_code == 403


async def test_void_forbidden_for_clerk(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    async with realdb.client(key="a", role="ap_manager") as mgr:
        memo_id = await _create_open_memo(mgr, vendor_id)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/credit-memos/{memo_id}/void")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# tenant isolation
# ---------------------------------------------------------------------------


async def test_tenant_isolation_list(realdb):
    # A memo created under tenant 'a' must not appear when tenant 'b' lists.
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    vendor_a = await _add_vendor(mk_a, org_a)
    async with realdb.client(key="a", role="ap_manager") as c:
        await _create_open_memo(c, vendor_a, number="CM-A-ONLY")

    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get("/api/credit-memos")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    # And tenant b's DB physically has no rows.
    mk_b = realdb.sessionmaker("b")
    async with mk_b() as s:
        count = (await s.execute(select(func.count()).select_from(CreditMemo))).scalar()
        assert count == 0


async def test_tenant_isolation_apply_cross_tenant(realdb):
    # A memo in tenant 'a' is invisible to tenant 'b' — apply returns 404.
    import uuid

    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    vendor_a = await _add_vendor(mk_a, org_a)
    async with realdb.client(key="a", role="ap_manager") as c:
        memo_id = await _create_open_memo(c, vendor_a)

    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.post(
            f"/api/credit-memos/{memo_id}/apply",
            json={"invoice_id": str(uuid.uuid4())},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Credit memo not found"


async def test_tenant_isolation_void_cross_tenant(realdb):
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    vendor_a = await _add_vendor(mk_a, org_a)
    async with realdb.client(key="a", role="ap_manager") as c:
        memo_id = await _create_open_memo(c, vendor_a)

    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.post(f"/api/credit-memos/{memo_id}/void")
    assert resp.status_code == 404
