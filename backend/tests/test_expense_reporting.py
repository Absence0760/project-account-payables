"""Real-DB coverage for the WF2 expense reporting additions:

  - GET  /api/expense-reports/{id}/summary  — total + count + by_category/by_status
  - GET  /api/expenses/export               — streamed expense-register CSV
  - POST /api/expenses/bulk-gl-code         — bulk GL re-code + per-expense audit

WF1's ``test_expenses.py`` covers the foundation CRUD; this file only exercises
the new endpoints, their money math (exact ``Numeric`` round-trips), the CSV
contract, audit rows, and RBAC.
"""

import uuid

from sqlalchemy import select

from app.models.expense import Expense
from app.models.gl_account import GLAccount
from app.models.workflow import AuditLog

# ---------------------------------------------------------------------------
# report summary
# ---------------------------------------------------------------------------


async def test_report_summary_math(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        e1 = (
            await c.post(
                "/api/expenses",
                json={"expense_date": "2026-06-01", "amount": "100.00", "category": "travel"},
            )
        ).json()["id"]
        e2 = (
            await c.post(
                "/api/expenses",
                json={"expense_date": "2026-06-02", "amount": "250.50", "category": "travel"},
            )
        ).json()["id"]
        e3 = (
            await c.post(
                "/api/expenses",
                json={"expense_date": "2026-06-03", "amount": "40.00", "category": "meals"},
            )
        ).json()["id"]

        rid = (await c.post("/api/expense-reports", json={"report_number": "SUM-001"})).json()["id"]
        attached = await c.post(
            f"/api/expense-reports/{rid}/expenses", json={"expense_ids": [e1, e2, e3]}
        )
        assert attached.status_code == 200, attached.text

        resp = await c.get(f"/api/expense-reports/{rid}/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 390.5  # 100 + 250.50 + 40
    assert body["count"] == 3

    by_cat = {r["category"]: r for r in body["by_category"]}
    assert by_cat["travel"]["count"] == 2
    assert by_cat["travel"]["total"] == 350.5
    assert by_cat["meals"]["count"] == 1
    assert by_cat["meals"]["total"] == 40.0

    # All three are draft (WF1 doesn't move status) → single by_status bucket.
    by_status = {r["status"]: r for r in body["by_status"]}
    assert by_status["draft"]["count"] == 3
    assert by_status["draft"]["total"] == 390.5


async def test_report_summary_empty(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid = (await c.post("/api/expense-reports", json={"report_number": "SUM-EMPTY"})).json()[
            "id"
        ]
        resp = await c.get(f"/api/expense-reports/{rid}/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0.0
    assert body["count"] == 0
    assert body["by_category"] == []
    assert body["by_status"] == []


async def test_report_summary_missing_404(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/expense-reports/{uuid.uuid4()}/summary")
    assert resp.status_code == 404


async def test_cfo_can_read_summary(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid = (await c.post("/api/expense-reports", json={"report_number": "SUM-CFO"})).json()["id"]
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get(f"/api/expense-reports/{rid}/summary")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

_EXPORT_HEADER = (
    "date,merchant,category,amount,currency,gl_code,payment_method,status,report_number"
)


async def test_export_csv_header_and_row(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        await c.post(
            "/api/expenses",
            json={
                "expense_date": "2026-06-09",
                "merchant": "Marriott",
                "category": "lodging",
                "amount": "812.34",
                "currency": "USD",
            },
        )
        resp = await c.get("/api/expenses/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="expenses_' in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.csv"')

    lines = resp.text.splitlines()
    assert lines[0] == _EXPORT_HEADER
    assert "Marriott" in resp.text
    assert "812.34" in resp.text
    # uncoded / unattached expense still emits a row (outer joins).
    row = next(line for line in lines[1:] if "Marriott" in line)
    assert "lodging" in row
    assert "out_of_pocket" in row
    assert "draft" in row


async def test_export_csv_status_filter(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        await c.post(
            "/api/expenses",
            json={"expense_date": "2026-06-01", "merchant": "FilterMe", "amount": "10.00"},
        )
        # everything is draft; submitted filter returns header-only.
        resp = await c.get("/api/expenses/export", params={"status": "submitted"})
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    assert lines[0] == _EXPORT_HEADER
    assert "FilterMe" not in resp.text


async def test_cfo_can_export(realdb):
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/expenses/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")


# ---------------------------------------------------------------------------
# bulk GL code
# ---------------------------------------------------------------------------


async def _mk_gl_account(realdb, key="a", code="6000", name="Travel Expense") -> uuid.UUID:
    mk = realdb.sessionmaker(key)
    org_id = realdb.info(key).org_id
    gl_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            GLAccount(
                id=gl_id,
                code=code,
                name=name,
                account_type="expense",
                organization_id=org_id,
            )
        )
        await s.commit()
    return gl_id


async def test_bulk_gl_code_sets_and_audits(realdb):
    mk = realdb.sessionmaker("a")
    gl_id = await _mk_gl_account(realdb)

    async with realdb.client(key="a", role="ap_clerk") as c:
        ids = [
            (
                await c.post(
                    "/api/expenses", json={"expense_date": "2026-06-01", "amount": "10.00"}
                )
            ).json()["id"]
            for _ in range(3)
        ]
        resp = await c.post(
            "/api/expenses/bulk-gl-code",
            json={"expense_ids": ids, "gl_account_id": str(gl_id)},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 3

    async with mk() as s:
        for raw in ids:
            e = (await s.execute(select(Expense).where(Expense.id == uuid.UUID(raw)))).scalar_one()
            assert e.gl_account_id == gl_id
        rows = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.action == "expense.bulk_gl_coded")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3


async def test_bulk_gl_code_clear(realdb):
    mk = realdb.sessionmaker("a")
    gl_id = await _mk_gl_account(realdb, code="6001", name="Other")
    async with realdb.client(key="a", role="ap_clerk") as c:
        eid = (
            await c.post(
                "/api/expenses",
                json={"expense_date": "2026-06-01", "amount": "10.00", "gl_account_id": str(gl_id)},
            )
        ).json()["id"]
        # Now clear it.
        resp = await c.post(
            "/api/expenses/bulk-gl-code",
            json={"expense_ids": [eid], "gl_account_id": None},
        )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    async with mk() as s:
        e = (await s.execute(select(Expense).where(Expense.id == uuid.UUID(eid)))).scalar_one()
        assert e.gl_account_id is None


async def test_bulk_gl_code_unknown_gl_404(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        eid = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "10.00"})
        ).json()["id"]
        resp = await c.post(
            "/api/expenses/bulk-gl-code",
            json={"expense_ids": [eid], "gl_account_id": str(uuid.uuid4())},
        )
    assert resp.status_code == 404


async def test_bulk_gl_code_unknown_expense_skipped_not_404(realdb):
    """An unresolvable id is skipped-and-reported, not a batch-wide 404 — the
    ``gl_account_id`` check (a real 404, applies to the whole batch) is
    unaffected; only the per-expense resolution partial-succeeds."""
    gl_id = await _mk_gl_account(realdb, code="6002")
    bad_id = str(uuid.uuid4())
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/expenses/bulk-gl-code",
            json={"expense_ids": [bad_id], "gl_account_id": str(gl_id)},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 0
    assert body["skipped"] == [{"id": bad_id, "reason": "not found"}]


async def test_bulk_gl_code_partial_success_does_not_roll_back(realdb):
    """A batch of 3 valid ids + 1 unresolvable one persists the 3 valid
    updates (never rolled back by the one bad row) and reports the failure
    with a reason — the invoice-bulk-endpoints partial-success contract."""
    mk = realdb.sessionmaker("a")
    gl_id = await _mk_gl_account(realdb, code="6003", name="Consulting")

    async with realdb.client(key="a", role="ap_clerk") as c:
        good_ids = [
            (
                await c.post(
                    "/api/expenses", json={"expense_date": "2026-06-01", "amount": "10.00"}
                )
            ).json()["id"]
            for _ in range(3)
        ]
        bad_id = str(uuid.uuid4())
        resp = await c.post(
            "/api/expenses/bulk-gl-code",
            json={"expense_ids": [*good_ids, bad_id], "gl_account_id": str(gl_id)},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 3
    assert body["skipped"] == [{"id": bad_id, "reason": "not found"}]

    # The 3 valid rows really were persisted — not rolled back by the bad id.
    async with mk() as s:
        for raw in good_ids:
            e = (await s.execute(select(Expense).where(Expense.id == uuid.UUID(raw)))).scalar_one()
            assert e.gl_account_id == gl_id
        rows = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.action == "expense.bulk_gl_coded")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3


async def test_bulk_gl_code_invalid_id_format_skipped(realdb):
    """A malformed (non-UUID) id in the batch is also a skip, not a
    batch-aborting 400 — the id-shape check used to raise before any row was
    ever touched."""
    gl_id = await _mk_gl_account(realdb, code="6004")
    async with realdb.client(key="a", role="ap_clerk") as c:
        good_id = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-01", "amount": "5.00"})
        ).json()["id"]
        resp = await c.post(
            "/api/expenses/bulk-gl-code",
            json={"expense_ids": [good_id, "not-a-uuid"], "gl_account_id": str(gl_id)},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 1
    assert body["skipped"] == [{"id": "not-a-uuid", "reason": "invalid id format"}]


async def test_cfo_cannot_bulk_gl_code(realdb):
    # CFO is read-only on mutations.
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post(
            "/api/expenses/bulk-gl-code",
            json={"expense_ids": [str(uuid.uuid4())], "gl_account_id": None},
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/expenses/ids — the "select all N matching" resolver
# ---------------------------------------------------------------------------


async def test_expense_ids_exceeds_a_single_list_page(realdb):
    """More matching expenses than one `GET /api/expenses` page (page_size
    20) — every id must come back, not just the first page's worth. This is
    the resolver behind the expenses page's "select all N matching"
    affordance, mirroring `test_invoice_ids.py`."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        created = []
        for i in range(25):
            r = await c.post(
                "/api/expenses",
                json={"expense_date": "2026-06-01", "amount": "5.00", "merchant": f"IdsCo{i}"},
            )
            created.append(r.json()["id"])

        resp = await c.get("/api/expenses/ids")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 25
    assert body["truncated"] is False
    assert set(body["ids"]) == set(created)


async def test_expense_ids_honours_status_and_search_filters(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        matching = (
            await c.post(
                "/api/expenses",
                json={"expense_date": "2026-06-01", "amount": "5.00", "merchant": "FilterTarget"},
            )
        ).json()["id"]
        await c.post(
            "/api/expenses",
            json={"expense_date": "2026-06-01", "amount": "5.00", "merchant": "SomethingElse"},
        )

        resp = await c.get("/api/expenses/ids", params={"search": "FilterTarget"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["ids"] == [matching]


async def test_expense_ids_truncates_past_the_cap(realdb, monkeypatch):
    monkeypatch.setattr("app.api.expenses.MAX_SELECT_ALL_IDS", 5, raising=True)
    async with realdb.client(key="a", role="ap_clerk") as c:
        for i in range(8):
            await c.post(
                "/api/expenses",
                json={"expense_date": "2026-06-01", "amount": "5.00", "merchant": f"CapCo{i}"},
            )
        resp = await c.get("/api/expenses/ids")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 8
    assert len(body["ids"]) == 5
    assert body["truncated"] is True
