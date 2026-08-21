"""Custom report builder — catalog, safe query engine, CRUD, export.

Covers the full ``/api/reports`` surface end-to-end against the live test
tenants, plus pure catalog/whitelist checks:

- the catalog shape (sources + their dimensions / measures / filters);
- a valid multi-dimension / multi-measure run returns the correct aggregates
  with money as an EXACT decimal string;
- the security invariant — a non-whitelisted data source / dimension / measure /
  aggregation / filter / operator is rejected with 422 and NEVER executed;
- tenant isolation (a run in tenant B can't see tenant A's rows);
- save / get / update / delete round-trip + the PII-free audit row each writes;
- branded CSV + PDF export return bytes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import AuditLog
from app.schemas.report import (
    DimensionSpec,
    FilterSpec,
    MeasureSpec,
    ReportSpec,
    SortSpec,
)
from app.services.report_builder import (
    ReportValidationError,
    build_catalog,
    compile_spec,
)

_TODAY = date.today()


# --------------------------------------------------------------------------- #
# Pure catalog + whitelist tests (no DB)
# --------------------------------------------------------------------------- #
def test_catalog_shape():
    cat = build_catalog()
    keys = {s["key"] for s in cat["sources"]}
    assert keys == {"invoices", "payments", "vendors", "expenses"}

    invoices = next(s for s in cat["sources"] if s["key"] == "invoices")
    dim_keys = {d["key"] for d in invoices["dimensions"]}
    assert {"vendor_name", "status", "invoice_date"} <= dim_keys
    # status is an enum dimension carrying its allowed values.
    status_dim = next(d for d in invoices["dimensions"] if d["key"] == "status")
    assert status_dim["type"] == "enum"
    assert "approved" in status_dim["enumValues"]

    measure_keys = {m["key"] for m in invoices["measures"]}
    assert {"amount", "id"} <= measure_keys
    amount_measure = next(m for m in invoices["measures"] if m["key"] == "amount")
    assert amount_measure["type"] == "money"
    assert "sum" in amount_measure["aggs"]

    filter_keys = {f["key"] for f in invoices["filters"]}
    assert {"status", "amount", "invoice_date"} <= filter_keys


def test_compile_valid_spec_maps_keys_to_columns():
    spec = ReportSpec(
        data_source="invoices",
        dimensions=[DimensionSpec(key="vendor_name")],
        measures=[MeasureSpec(key="amount", agg="sum"), MeasureSpec(key="id", agg="count")],
        filters=[FilterSpec(key="status", op="in", value=["approved", "paid"])],
        sort=[SortSpec(key="amount_sum", dir="desc")],
    )
    plan = compile_spec(spec)
    assert [d.key for d in plan.dimensions] == ["vendor_name"]
    assert [(m.out_key, m.type) for m in plan.measures] == [
        ("amount_sum", "money"),
        ("id_count", "number"),
    ]


@pytest.mark.parametrize(
    "spec",
    [
        # unknown data source (SQL-injection-shaped)
        ReportSpec(data_source="invoices; DROP TABLE invoices"),
        # unknown dimension key
        ReportSpec(
            data_source="invoices",
            dimensions=[DimensionSpec(key="(SELECT 1)")],
            measures=[MeasureSpec(key="id", agg="count")],
        ),
        # measure that exists but a disallowed aggregation
        ReportSpec(
            data_source="invoices",
            measures=[MeasureSpec(key="id", agg="sum")],  # id only allows count
        ),
        # unknown aggregation
        ReportSpec(
            data_source="invoices",
            measures=[MeasureSpec(key="amount", agg="exec")],
        ),
        # unknown filter key
        ReportSpec(
            data_source="invoices",
            measures=[MeasureSpec(key="id", agg="count")],
            filters=[FilterSpec(key="secret_column", op="eq", value="x")],
        ),
        # operator not allowed on that filter type (contains on money)
        ReportSpec(
            data_source="invoices",
            measures=[MeasureSpec(key="id", agg="count")],
            filters=[FilterSpec(key="amount", op="contains", value="1")],
        ),
        # unknown date grain
        ReportSpec(
            data_source="invoices",
            dimensions=[DimensionSpec(key="invoice_date", grain="fortnight")],
            measures=[MeasureSpec(key="id", agg="count")],
        ),
        # sort by a column that isn't selected
        ReportSpec(
            data_source="invoices",
            measures=[MeasureSpec(key="id", agg="count")],
            sort=[SortSpec(key="amount_sum")],
        ),
    ],
)
def test_out_of_catalog_specs_are_rejected(spec):
    with pytest.raises(ReportValidationError):
        compile_spec(spec)


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
async def _default_entity_id(s):
    return (
        await s.execute(select(Entity.id).where(Entity.is_default.is_(True)).limit(1))
    ).scalar_one()


async def _add_many_invoices(mk, org_id, *, count, amount="10.00", prefix="Bulk"):
    """Bulk-create `count` invoices with distinct vendor names (one commit) so
    a group-by-vendor report produces `count` distinct rows — used to exercise
    the export row cap without one commit per row."""
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        s.add_all(
            [
                Invoice(
                    organization_id=org_id,
                    entity_id=entity_id,
                    invoice_number=f"{prefix}-{i:05d}",
                    vendor_name=f"{prefix}Vendor{i:05d}",
                    amount=Decimal(amount),
                    currency="USD",
                    invoice_date=_TODAY,
                    due_date=_TODAY + timedelta(days=30),
                    status=InvoiceStatus.approved,
                )
                for i in range(count)
            ]
        )
        await s.commit()


async def _add_invoice(
    mk,
    org_id,
    *,
    vendor_name,
    amount,
    status=InvoiceStatus.approved,
    num=None,
    invoice_date=None,
    created_at=None,
):
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            entity_id=await _default_entity_id(s),
            invoice_number=num or f"INV-{uuid.uuid4().hex[:8]}",
            vendor_name=vendor_name,
            amount=Decimal(amount),
            currency="USD",
            invoice_date=invoice_date or _TODAY,
            due_date=_TODAY + timedelta(days=30),
            status=status,
        )
        if created_at is not None:
            # Override the server_default so a test can pin the instant a row
            # was recorded at (the timestamp-vs-date filter cases below).
            inv.created_at = created_at
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return str(inv.id)


# --------------------------------------------------------------------------- #
# Catalog over HTTP
# --------------------------------------------------------------------------- #
async def test_catalog_endpoint(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/reports/catalog")
    assert resp.status_code == 200, resp.text
    keys = {s["key"] for s in resp.json()["sources"]}
    assert keys == {"invoices", "payments", "vendors", "expenses"}


# --------------------------------------------------------------------------- #
# Valid run — aggregates + exact-string money
# --------------------------------------------------------------------------- #
async def test_run_aggregates_exact_money(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_invoice(mk, org_id, vendor_name="Acme", amount="100.00")
    await _add_invoice(mk, org_id, vendor_name="Acme", amount="200.50")
    await _add_invoice(mk, org_id, vendor_name="Globex", amount="50.00")

    body = {
        "data_source": "invoices",
        "dimensions": [{"key": "vendor_name"}],
        "measures": [{"key": "amount", "agg": "sum"}, {"key": "id", "agg": "count"}],
        "filters": [{"key": "status", "op": "in", "value": ["approved", "paid"]}],
        "sort": [{"key": "amount_sum", "dir": "desc"}],
    }
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post("/api/reports/run", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Columns carry the money/number typing.
    col_types = {col["key"]: col.get("type") for col in data["columns"]}
    assert col_types["amount_sum"] == "money"
    assert col_types["id_count"] == "number"

    rows = {r["vendor_name"]: r for r in data["rows"]}
    assert rows["Acme"]["amount_sum"] == "300.50"  # exact decimal STRING
    assert isinstance(rows["Acme"]["amount_sum"], str)
    assert rows["Acme"]["id_count"] == 2
    assert rows["Globex"]["amount_sum"] == "50.00"
    # sorted desc by amount_sum → Acme first.
    assert data["rows"][0]["vendor_name"] == "Acme"
    assert data["total_rows"] == 2


# --------------------------------------------------------------------------- #
# Security — out-of-catalog references rejected at the HTTP boundary (422)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "body",
    [
        {"data_source": "invoices); DROP TABLE invoices;--"},
        {
            "data_source": "invoices",
            "dimensions": [{"key": "1;DELETE FROM invoices"}],
            "measures": [{"key": "id", "agg": "count"}],
        },
        {"data_source": "invoices", "measures": [{"key": "amount", "agg": "system"}]},
        {
            "data_source": "invoices",
            "measures": [{"key": "id", "agg": "count"}],
            "filters": [{"key": "password", "op": "eq", "value": "x"}],
        },
        {
            "data_source": "invoices",
            "measures": [{"key": "id", "agg": "count"}],
            "filters": [{"key": "amount", "op": "regex", "value": "x"}],
        },
    ],
)
async def test_non_whitelisted_run_rejected_422(realdb, body):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post("/api/reports/run", json=body)
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# Tenant isolation — a run only sees its own tenant's rows
# --------------------------------------------------------------------------- #
async def test_run_is_tenant_isolated(realdb):
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    await _add_invoice(mk_a, org_a, vendor_name="IsoVendorA", amount="999.99")

    body = {
        "data_source": "invoices",
        "dimensions": [{"key": "vendor_name"}],
        "measures": [{"key": "amount", "agg": "sum"}],
        "filters": [{"key": "vendor_name", "op": "eq", "value": "IsoVendorA"}],
    }

    async with realdb.client(key="a", role="admin") as c:
        resp_a = await c.post("/api/reports/run", json=body)
    assert resp_a.status_code == 200, resp_a.text
    assert any(r["vendor_name"] == "IsoVendorA" for r in resp_a.json()["rows"])

    # Same spec, tenant B — must NOT see tenant A's vendor.
    async with realdb.client(key="b", role="admin") as c:
        resp_b = await c.post("/api/reports/run", json=body)
    assert resp_b.status_code == 200, resp_b.text
    assert resp_b.json()["total_rows"] == 0
    assert resp_b.json()["rows"] == []


# --------------------------------------------------------------------------- #
# Saved-definition CRUD + audit rows
# --------------------------------------------------------------------------- #
async def test_report_crud_and_audit(realdb):
    mk = realdb.sessionmaker("a")
    save_body = {
        "name": "Spend by vendor",
        "description": "Monthly vendor spend",
        "data_source": "invoices",
        "dimensions": [{"key": "vendor_name"}],
        "measures": [{"key": "amount", "agg": "sum"}],
        "filters": [],
        "sort": [{"key": "amount_sum", "dir": "desc"}],
    }
    async with realdb.client(key="a", role="ap_manager") as c:
        created = await c.post("/api/reports", json=save_body)
        assert created.status_code == 201, created.text
        report_id = created.json()["id"]
        assert created.json()["name"] == "Spend by vendor"
        assert created.json()["data_source"] == "invoices"

        # Appears in the list.
        listed = await c.get("/api/reports")
        assert any(r["id"] == report_id for r in listed.json()["reports"])

        # Detail.
        detail = await c.get(f"/api/reports/{report_id}")
        assert detail.status_code == 200
        assert detail.json()["measures"] == [{"key": "amount", "agg": "sum"}]

        # Update the name.
        patched = await c.patch(f"/api/reports/{report_id}", json={"name": "Renamed report"})
        assert patched.status_code == 200
        assert patched.json()["name"] == "Renamed report"

        # Delete.
        deleted = await c.delete(f"/api/reports/{report_id}")
        assert deleted.status_code == 204

        gone = await c.get(f"/api/reports/{report_id}")
        assert gone.status_code == 404

    # Audit rows for create / update / delete — PII-free.
    async with mk() as s:
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(
                        AuditLog.entity_type == "report_definition",
                        AuditLog.entity_id == uuid.UUID(report_id),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {"report.created", "report.updated", "report.deleted"} <= set(actions)


async def test_create_report_rejects_bad_spec_422(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/reports",
            json={
                "name": "bad",
                "data_source": "invoices",
                "measures": [{"key": "amount", "agg": "nope"}],
            },
        )
    assert resp.status_code == 422, resp.text


async def test_clerk_cannot_save_report(realdb):
    """Read RBAC is all four roles; mutating is admin/ap_manager/cfo — a clerk
    can run but not save."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/reports",
            json={
                "name": "clerk report",
                "data_source": "invoices",
                "measures": [{"key": "id", "agg": "count"}],
            },
        )
    assert resp.status_code == 403, resp.text


# --------------------------------------------------------------------------- #
# Export — branded CSV + PDF bytes
# --------------------------------------------------------------------------- #
async def test_export_csv_and_pdf(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_invoice(mk, org_id, vendor_name="ExportCo", amount="123.45")

    save_body = {
        "name": "Export report",
        "data_source": "invoices",
        "dimensions": [{"key": "vendor_name"}],
        "measures": [{"key": "amount", "agg": "sum"}],
    }
    async with realdb.client(key="a", role="cfo") as c:
        created = await c.post("/api/reports", json=save_body)
        report_id = created.json()["id"]

        csv_resp = await c.get(f"/api/reports/{report_id}/export?format=csv")
        assert csv_resp.status_code == 200, csv_resp.text
        assert csv_resp.headers["content-type"].startswith("text/csv")
        body = csv_resp.text
        # Brand provenance comment block + the data grid with our row.
        assert body.lstrip().startswith("#")
        assert "ExportCo" in body
        assert "123.45" in body
        # Under the 1000-row export cap: no spurious truncation note.
        assert "truncated" not in body.lower()

        pdf_resp = await c.get(f"/api/reports/{report_id}/export?format=pdf")
        assert pdf_resp.status_code == 200, pdf_resp.text
        assert pdf_resp.headers["content-type"] == "application/pdf"
        assert pdf_resp.content[:4] == b"%PDF"


async def test_export_over_cap_surfaces_truncation_note(realdb):
    """A report matching more rows than the 1000-row export cap must say so in
    the file itself — a CFO exporting a large dataset should never get a
    quietly incomplete CSV/PDF. Regression test for issue #131 part 1."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_many_invoices(mk, org_id, count=1001, prefix="Cap")

    save_body = {
        "name": "Over-cap report",
        "data_source": "invoices",
        "dimensions": [{"key": "vendor_name"}],
        "measures": [{"key": "amount", "agg": "sum"}],
    }
    async with realdb.client(key="a", role="cfo") as c:
        created = await c.post("/api/reports", json=save_body)
        report_id = created.json()["id"]

        csv_resp = await c.get(f"/api/reports/{report_id}/export?format=csv")
        assert csv_resp.status_code == 200, csv_resp.text
        body = csv_resp.text
        assert "truncated at 1000 rows" in body.lower()
        assert "1001" in body  # the true matching-row count, not just the cap

        pdf_resp = await c.get(f"/api/reports/{report_id}/export?format=pdf")
        assert pdf_resp.status_code == 200, pdf_resp.text
        assert pdf_resp.content[:4] == b"%PDF"


# --------------------------------------------------------------------------- #
# Date filters over a TIMESTAMP column cover the whole calendar day
# --------------------------------------------------------------------------- #
async def _run_filtered(realdb, filters, *, vendor):
    body = {
        "data_source": "invoices",
        "dimensions": [{"key": "vendor_name"}],
        "measures": [{"key": "amount", "agg": "sum"}],
        "filters": [{"key": "vendor_name", "op": "eq", "value": vendor}, *filters],
    }
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post("/api/reports/run", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["rows"]


async def test_date_filter_on_timestamp_column_covers_the_whole_day(realdb):
    """``created_at`` is a TIMESTAMP, not a DATE. Binding a bare date onto it
    resolves to that day's MIDNIGHT, so ``lte`` / ``between`` / ``eq`` answered
    the wrong question — an invoice recorded at 15:30 today was invisible to a
    report filtered "created_at up to today", while ``gt today`` wrongly swept
    it in. Every operator now compares calendar days via half-open
    ``[day, day+1)`` bounds."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    stamp = datetime.now(UTC).replace(hour=15, minute=30, second=0, microsecond=0)
    day = stamp.date().isoformat()
    yesterday = (stamp.date() - timedelta(days=1)).isoformat()
    tomorrow = (stamp.date() + timedelta(days=1)).isoformat()
    await _add_invoice(mk, org_id, vendor_name="AfternoonCo", amount="77.00", created_at=stamp)

    # Inclusive of the day itself.
    for filt in (
        {"key": "created_at", "op": "eq", "value": day},
        {"key": "created_at", "op": "lte", "value": day},
        {"key": "created_at", "op": "gte", "value": day},
        {"key": "created_at", "op": "between", "value": [yesterday, day]},
        {"key": "created_at", "op": "between", "value": [day, day]},
    ):
        rows = await _run_filtered(realdb, [filt], vendor="AfternoonCo")
        assert rows and rows[0]["amount_sum"] == "77.00", f"{filt} lost the row"

    # Exclusive of the day itself — the mirror image, which the midnight
    # comparison got backwards for ``gt``.
    for filt in (
        {"key": "created_at", "op": "gt", "value": day},
        {"key": "created_at", "op": "lt", "value": day},
        {"key": "created_at", "op": "ne", "value": day},
        {"key": "created_at", "op": "between", "value": [tomorrow, tomorrow]},
    ):
        rows = await _run_filtered(realdb, [filt], vendor="AfternoonCo")
        assert rows == [], f"{filt} should not match a row stamped on {day}"


async def test_date_filter_on_real_date_column_is_unchanged(realdb):
    """``invoice_date`` IS a DATE column — the calendar-day translation must
    not disturb it. Same operators, same inclusive semantics."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    day = _TODAY.isoformat()
    await _add_invoice(mk, org_id, vendor_name="PlainDateCo", amount="12.00", invoice_date=_TODAY)

    for filt in (
        {"key": "invoice_date", "op": "eq", "value": day},
        {"key": "invoice_date", "op": "lte", "value": day},
        {"key": "invoice_date", "op": "between", "value": [day, day]},
    ):
        rows = await _run_filtered(realdb, [filt], vendor="PlainDateCo")
        assert rows and rows[0]["amount_sum"] == "12.00", f"{filt} lost the row"

    rows = await _run_filtered(
        realdb, [{"key": "invoice_date", "op": "gt", "value": day}], vendor="PlainDateCo"
    )
    assert rows == []


def test_date_in_op_would_also_be_day_scoped():
    """No shipped date filter allows ``in`` today (``_DATE_OPS`` omits it), but
    the day translation covers it so *adding* ``in`` to a date filter's ops
    can't silently reintroduce the midnight-comparison bug. Asserted at the
    clause level since the catalog gives no route to it."""
    from app.services.report_builder import FilterDef, _build_where

    fdef = FilterDef("created_at", "Created", "date", Invoice.created_at, ("in",))
    clause = str(_build_where(fdef, "in", ["2026-06-30", "2026-07-01"]))
    # Two half-open windows OR'd together — never a bare `IN (...)` of dates.
    assert "IN " not in clause.upper()
    assert clause.count(">=") == 2 and clause.count("<") >= 2
