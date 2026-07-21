"""CSV formula-injection guard (CWE-1236) — `report_export.csv_safe_cell`.

Every CSV export surface (report_export exporters, /api/audit export,
/api/reports export, /api/invoices bulk export, /api/workflow invoice export)
runs attacker-influenced text cells (vendor name from AI extraction, user full
name, description) through the shared `csv_safe_cell` helper so a value
starting with `=`, `+`, `-`, `@`, tab or CR can't execute as a spreadsheet
formula when a CFO opens the file in Excel.

Two invariants pinned here:
  1. dangerous string cells come out `'`-prefixed;
  2. money never gets mangled — signed numeric strings (anything `Decimal`
     parses, e.g. "-12.34") and non-string cells pass through byte-identical.

The endpoint-level bulk-export leg lives in
`test_bulk_export_csv_injection.py` (realdb). Positive Pay files
(`services/positive_pay_adapters/`) are deliberately NOT escaped —
fixed-format bank-machine uploads, never opened as a spreadsheet; a `'`
prefix would break the bank's payee exact-match. See the note in
`csv_formatter.py`.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.audit import _entries_to_csv
from app.schemas.audit import AuditExportEntry
from app.services.report_export import (
    csv_safe_cell,
    export_invoice_register,
    export_vendor_spend,
    safe_csv_writer,
)


def _read(csv_text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(csv_text)))


# ---------------------------------------------------------------------------
# csv_safe_cell — the shared helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        '=cmd|" /C calc"!A0',
        "=1+2",
        "+cmd()",
        "-2+3+cmd",
        "@SUM(A1:A9)",
        "\t=1+2",
        "\r=1+2",
    ],
)
def test_csv_safe_cell_escapes_each_dangerous_prefix(value):
    assert csv_safe_cell(value) == "'" + value


@pytest.mark.parametrize(
    "value",
    [
        "Acme Corp",
        "O'Brien & Sons",  # apostrophe elsewhere is fine
        "1234.56",
        "INV-2026-001",
        "",
        " =1+2",  # leading space already neutralizes the formula
    ],
)
def test_csv_safe_cell_leaves_safe_strings_untouched(value):
    assert csv_safe_cell(value) == value


@pytest.mark.parametrize(
    "value",
    [None, 5, 0, Decimal("-12.34"), 1.5, date(2026, 1, 2), datetime(2026, 1, 2, tzinfo=UTC), True],
)
def test_csv_safe_cell_passes_non_strings_through(value):
    assert csv_safe_cell(value) is value


@pytest.mark.parametrize("value", ["-12.34", "-1", "-0.05", "-1000000.00", "+1234567", "-1e5"])
def test_csv_safe_cell_never_mangles_signed_numeric_strings(value):
    """A signed value `Decimal` parses is data, not a formula — a spreadsheet
    evaluates `+1234567` to the same constant, so escaping would only mangle
    phone-number / scientific-notation cells."""
    assert csv_safe_cell(value) == value


@pytest.mark.parametrize("value", ["-12.34abc", "-12.34+cmd", "- 12.34"])
def test_csv_safe_cell_escapes_sign_prefixed_non_numbers(value):
    assert csv_safe_cell(value) == "'" + value


def test_safe_csv_writer_sanitizes_every_cell():
    buf = io.StringIO()
    safe_csv_writer(buf).writerow(["=evil", "-12.34", "", Decimal("5.00"), "ok"])
    assert _read(buf.getvalue())[0] == ["'=evil", "-12.34", "", "5.00", "ok"]


# ---------------------------------------------------------------------------
# report_export exporters (analytics / scheduled reports)
# ---------------------------------------------------------------------------


def test_invoice_register_escapes_malicious_vendor_and_keeps_amount_exact():
    inv = SimpleNamespace(
        id=uuid.uuid4(),
        invoice_number='=HYPERLINK("http://evil","click")',
        vendor_name='=cmd|" /C calc"!A0',
        amount=Decimal("-12.34"),
        currency="USD",
        status="approved",
        invoice_date=date(2026, 1, 5),
        due_date=None,
        created_at=None,
        po_number="@PO-1",
    )
    rows = _read(export_invoice_register([inv]))
    row = dict(zip(rows[0], rows[1]))
    assert row["vendor_name"] == "'" + '=cmd|" /C calc"!A0'
    assert row["invoice_number"].startswith("'=")
    assert row["po_number"] == "'@PO-1"
    # negative Decimal money survives byte-for-byte
    assert row["amount"] == "-12.34"


def test_vendor_spend_escapes_vendor_name_only():
    rows = _read(export_vendor_spend([("+malicious()", 3, Decimal("-99.50"))]))
    # Trailing "" is the `currencies` column: a positional 3-tuple caller
    # carries no currency info, so it exports blank by design (the real caller
    # is `vendor_rollup_to_reporting_currency`, which supplies it).
    assert rows[0] == ["vendor_name", "invoice_count", "total_amount", "currencies"]
    assert rows[1] == ["'+malicious()", "3", "-99.50", ""]


# ---------------------------------------------------------------------------
# /api/audit CSV export (actor_name is control-plane user input)
# ---------------------------------------------------------------------------


def test_audit_entries_to_csv_escapes_actor_name():
    entry = AuditExportEntry(
        id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        actor_id=str(uuid.uuid4()),
        actor_name='=WEBSERVICE("http://evil/x")',
        actor_email="user@example.com",
        action="invoice.approved",
        entity_type="invoice",
        entity_id=str(uuid.uuid4()),
        details=None,
        created_at="2026-01-02T03:04:05Z",
    )
    rows = _read(_entries_to_csv([entry]))
    row = dict(zip(rows[0], rows[1]))
    assert row["actor_name"] == '\'=WEBSERVICE("http://evil/x")'
    assert row["actor_email"] == "user@example.com"
    assert row["action"] == "invoice.approved"
