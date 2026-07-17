"""Real-DB coverage for the expenses + expense-reports routers.

Covers ``backend/app/api/expenses.py`` end-to-end against the live test
tenants: expense CRUD, receipt upload + download round-trip, report
create/attach/detach with exact ``total_amount`` recompute, RBAC, tenant
isolation, audit rows, and exact ``Numeric`` money round-trips. A passing
upload/CRUD run against the ``realdb`` fixture (whose tenant tables are built
via ``Base.metadata.create_all``) is the create_all parity proof for all five
new tables incl. the circular FK; an explicit table-existence test pins it.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select, text

from app.models.expense import Expense, ExpenseReport
from app.models.workflow import AuditLog

# ---------------------------------------------------------------------------
# create_all parity
# ---------------------------------------------------------------------------


async def test_all_expense_tables_exist(realdb):
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        for t in (
            "expense_reports",
            "expenses",
            "expense_policies",
            "corporate_card_transactions",
            "expense_preapprovals",
        ):
            await s.execute(text(f"SELECT 1 FROM {t} LIMIT 1"))  # raises if missing


# ---------------------------------------------------------------------------
# expense CRUD
# ---------------------------------------------------------------------------


async def test_create_expense(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/expenses",
            json={
                "expense_date": "2026-06-01",
                "merchant": "Uber",
                "category": "travel",
                "amount": "42.50",
                "currency": "USD",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["merchant"] == "Uber"
    assert body["amount"] == 42.5
    assert body["status"] == "draft"
    assert body["payment_method"] == "out_of_pocket"

    # Exact Decimal round-trip through Numeric(15, 2).
    async with mk() as s:
        e = (await s.execute(select(Expense))).scalar_one()
        assert e.amount == Decimal("42.50")
        assert e.organization_id == org_id
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.entity_type == "expense")))
            .scalars()
            .all()
        )
        assert "expense.created" in actions


async def test_create_expense_rejects_nonpositive_amount(realdb):
    """A negative / zero expense is a 422 — it must never net a report under the
    CFO approval threshold while hiding a genuinely large line (issue #156)."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        for bad in ("-5001.00", "0", "0.00"):
            resp = await c.post(
                "/api/expenses",
                json={"expense_date": "2026-06-01", "amount": bad},
            )
            assert resp.status_code == 422, f"{bad}: {resp.text}"

        # And a PATCH cannot flip a valid expense negative afterwards.
        eid = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "10.00"})
        ).json()["id"]
        patched = await c.patch(f"/api/expenses/{eid}", json={"amount": "-1.00"})
        assert patched.status_code == 422, patched.text


async def test_list_filter_and_get(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "10.00"})
        eid = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-02", "amount": "20.00"})
        ).json()["id"]

        listing = await c.get("/api/expenses")
        assert listing.status_code == 200
        assert listing.json()["total"] >= 2

        one = await c.get(f"/api/expenses/{eid}")
        assert one.status_code == 200
        assert one.json()["amount"] == 20.0


async def test_update_expense(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        eid = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "10.00"})
        ).json()["id"]
        resp = await c.patch(f"/api/expenses/{eid}", json={"amount": "55.00", "merchant": "Lyft"})
    assert resp.status_code == 200
    assert resp.json()["amount"] == 55.0
    assert resp.json()["merchant"] == "Lyft"

    async with mk() as s:
        e = (await s.execute(select(Expense).where(Expense.id == uuid.UUID(eid)))).scalar_one()
        assert e.amount == Decimal("55.00")
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.action == "expense.updated")))
            .scalars()
            .all()
        )
        assert len(actions) >= 1


async def test_delete_expense(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        eid = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "10.00"})
        ).json()["id"]
        gone = await c.delete(f"/api/expenses/{eid}")
        assert gone.status_code == 204
        missing = await c.get(f"/api/expenses/{eid}")
        assert missing.status_code == 404


# ---------------------------------------------------------------------------
# receipt upload + download proxy
# ---------------------------------------------------------------------------


async def test_receipt_upload_roundtrip(realdb):
    org_id = realdb.info("a").org_id
    async with realdb.client(key="a", role="ap_clerk") as c:
        eid = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "10.00"})
        ).json()["id"]
        resp = await c.post(
            f"/api/expenses/{eid}/receipt",
            files={"file": ("receipt.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        key = resp.json()["receipt_file_key"]
        assert key.startswith(f"{org_id}/expenses/{eid}/")
        assert resp.json()["receipt_url"] == f"/api/expenses/receipt/{key}"

        got = await c.get(f"/api/expenses/receipt/{key}")
        assert got.status_code == 200
        assert got.content == b"%PDF-1.4 fake"


async def test_receipt_cross_tenant_404(realdb):
    org_a = realdb.info("a").org_id
    async with realdb.client(key="a", role="ap_clerk") as c:
        eid = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "10.00"})
        ).json()["id"]
        key = (
            await c.post(
                f"/api/expenses/{eid}/receipt",
                files={"file": ("r.pdf", b"%PDF-1.4 fake", "application/pdf")},
            )
        ).json()["receipt_file_key"]

    # Tenant B (different org_id prefix) cannot read tenant A's receipt key.
    assert key.startswith(f"{org_a}/")
    async with realdb.client(key="b", role="ap_clerk") as c:
        denied = await c.get(f"/api/expenses/receipt/{key}")
        assert denied.status_code == 404


# ---------------------------------------------------------------------------
# expense reports — create + attach/detach + total recompute
# ---------------------------------------------------------------------------


async def test_report_attach_recomputes_total(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        e1 = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "100.00"})
        ).json()["id"]
        e2 = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-02", "amount": "250.50"})
        ).json()["id"]

        report = (
            await c.post(
                "/api/expense-reports",
                json={"report_number": "EXP-2026-001", "title": "June travel"},
            )
        ).json()
        rid = report["id"]
        assert report["total_amount"] == 0.0
        assert report["employee_user_id"]  # defaulted to the caller

        attached = await c.post(
            f"/api/expense-reports/{rid}/expenses", json={"expense_ids": [e1, e2]}
        )
        assert attached.status_code == 200, attached.text
        assert attached.json()["total_amount"] == 350.5
        assert len(attached.json()["expenses"]) == 2

        # Detach one → total recomputes.
        detached = await c.post(
            f"/api/expense-reports/{rid}/expenses",
            json={"expense_ids": [e1], "detach": True},
        )
        assert detached.json()["total_amount"] == 250.5
        assert len(detached.json()["expenses"]) == 1

    async with mk() as s:
        r = (
            await s.execute(select(ExpenseReport).where(ExpenseReport.id == uuid.UUID(rid)))
        ).scalar_one()
        assert r.total_amount == Decimal("250.50")  # exact, not float
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.entity_type == "expense_report")
                )
            )
            .scalars()
            .all()
        )
        assert "expense_report.created" in actions
        assert "expense_report.expenses_attached" in actions


async def test_attach_moves_expense_recomputes_source_report(realdb):
    """Reassigning an expense from report A to report B must recompute A's
    total too — not just B's — or A's Numeric total goes stale."""
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        e1 = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "100.00"})
        ).json()["id"]
        e2 = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-02", "amount": "40.00"})
        ).json()["id"]

        ra = (await c.post("/api/expense-reports", json={"report_number": "MOVE-A"})).json()["id"]
        rb = (await c.post("/api/expense-reports", json={"report_number": "MOVE-B"})).json()["id"]

        # Both expenses start on report A → total 140.00.
        attached = await c.post(
            f"/api/expense-reports/{ra}/expenses", json={"expense_ids": [e1, e2]}
        )
        assert attached.json()["total_amount"] == 140.0

        # Move e1 onto report B. B gains 100; A must drop to 40 (not stay 140).
        moved = await c.post(f"/api/expense-reports/{rb}/expenses", json={"expense_ids": [e1]})
        assert moved.status_code == 200, moved.text
        assert moved.json()["total_amount"] == 100.0

        report_a = (await c.get(f"/api/expense-reports/{ra}")).json()
        assert report_a["total_amount"] == 40.0
        assert len(report_a["expenses"]) == 1

    async with mk() as s:
        a = (
            await s.execute(select(ExpenseReport).where(ExpenseReport.id == uuid.UUID(ra)))
        ).scalar_one()
        assert a.total_amount == Decimal("40.00")  # exact, recomputed


async def test_create_expense_under_report_updates_total(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid = (await c.post("/api/expense-reports", json={"report_number": "EXP-2026-002"})).json()[
            "id"
        ]
        # Creating an expense already pointed at the report bumps the total.
        await c.post(
            "/api/expenses",
            json={"expense_date": "2026-06-01", "amount": "75.00", "report_id": rid},
        )
        report = (await c.get(f"/api/expense-reports/{rid}")).json()
    assert report["total_amount"] == 75.0


# ---------------------------------------------------------------------------
# RBAC + tenant isolation
# ---------------------------------------------------------------------------


async def test_cfo_cannot_create_expense(realdb):
    # CFO is read-only on mutations.
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "5.00"})
    assert resp.status_code == 403


async def test_cfo_can_read_expenses(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "5.00"})
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/expenses")
    assert resp.status_code == 200


async def test_tenant_isolation(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        eid = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "5.00"})
        ).json()["id"]
    async with realdb.client(key="b", role="ap_manager") as c:
        assert (await c.get(f"/api/expenses/{eid}")).status_code == 404
