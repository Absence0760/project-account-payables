"""Regression tests for issue #120 — non-exhaustive report dispatch in
`GET /api/analytics/export/{report}`.

The `if/elif/.../else` dispatch had no branch for `expense_register` even
though it's a registered `EXPORTERS` key (and named in the endpoint's own 404
message) — it fell into the `else: # aging_snapshot` branch and fed the aging
bucket dict into `export_expense_register`, producing a CSV with every real
column blank and single characters from the bucket-key strings
("current", "days_30", ...) landing in `report_number`/`gl_code`. Returned
HTTP 200 with no error signal.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

TENANT = "a"


@pytest.mark.asyncio
async def test_expense_register_export_returns_real_expense_rows(realdb):
    """The exported CSV must contain the actual expense (merchant, category,
    amount) — not the aging bucket dict's keys sliced into the columns."""
    from app.models.gl_account import GLAccount

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    async with mk() as s:
        gl = GLAccount(organization_id=org_id, code="6100", name="Travel", account_type="expense")
        s.add(gl)
        await s.commit()

    async with realdb.client(key=TENANT, role="ap_clerk") as c:
        create_resp = await c.post(
            "/api/expenses",
            json={
                "expense_date": (date.today() - timedelta(days=5)).isoformat(),
                "merchant": "Delta Airlines",
                "category": "travel",
                "amount": "482.13",
                "currency": "USD",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        expense_id = create_resp.json()["id"]

    async with mk() as s:
        from app.models.expense import Expense

        exp = (await s.execute(select(Expense).where(Expense.id == expense_id))).scalar_one()
        exp.gl_account_id = gl.id
        await s.commit()

    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.get("/api/analytics/export/expense_register")

    assert resp.status_code == 200
    lines = resp.text.splitlines()
    # Brand-provenance comment block precedes the real grid header.
    start = next(i for i, ln in enumerate(lines) if ln.startswith("date,merchant"))
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    assert len(rows) == 1
    row = rows[0]
    assert row["merchant"] == "Delta Airlines"
    assert row["category"] == "travel"
    assert Decimal(row["amount"]) == Decimal("482.13")
    assert row["gl_code"] == "6100"
    # The old bug: the aging bucket dict's string keys ("current", "days_30",
    # ...) got sliced character-by-character into these columns instead.
    assert row["merchant"] not in ("c", "u", "r", "d", "a")


@pytest.mark.asyncio
async def test_expense_register_export_empty_still_returns_real_header(realdb):
    """With no expenses in the period, the export must still carry the
    expense-register header/shape — not silently become an aging snapshot."""
    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.get("/api/analytics/export/expense_register?period_days=1")

    assert resp.status_code == 200
    lines = resp.text.splitlines()
    header_line = next(ln for ln in lines if ln.startswith("date,merchant"))
    assert "gl_code" in header_line
    assert "report_number" in header_line
    # Must NOT be the aging_snapshot header.
    assert not any(ln.startswith("as_of_date") for ln in lines)
