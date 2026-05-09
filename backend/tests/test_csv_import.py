"""Unit tests for the pilot-data CSV importers.

DB-free: the async session is replaced with a mock that always reports
"row not found" on select, and captures what gets added. Coverage
scope is parsing + row-level validation + dedup branching. The SQL
execution path (real ``INSERT`` round-trip) is not covered by any
automated test — verify manually against seed data or a staging tenant
when changing the service. See ``backend/scripts/seed.py``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.csv_import import (
    _parse_bool,
    _parse_date,
    _parse_decimal,
    import_invoices_csv,
    import_vendors_csv,
)


class _StubSession:
    """Minimal AsyncSession stub: always returns None on select, tracks added objects."""

    def __init__(self, existing=()):
        self._existing = list(existing)
        self.added: list = []
        self.flushed: int = 0

    async def execute(self, _stmt):
        # Always say "no matching row" — importer treats everything as new.
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        return result

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("Y", True),
        ("0", False),
        ("false", False),
        ("", False),
        (None, False),
    ],
)
def test_parse_bool(raw, expected):
    assert _parse_bool(raw) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1234.56", Decimal("1234.56")),
        ("$1,234.56", Decimal("1234.56")),
        ("1,000", Decimal("1000")),
        ("", None),
        (None, None),
        ("not-a-number", None),
    ],
)
def test_parse_decimal(raw, expected):
    assert _parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["2026-04-23", "04/23/2026", "23/04/2026", "2026/04/23"],
)
def test_parse_date_accepts_common_formats(raw):
    d = _parse_date(raw)
    assert d is not None
    assert d.year == 2026 and d.month == 4 and d.day == 23


def test_parse_date_returns_none_for_junk():
    assert _parse_date("") is None
    assert _parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# Vendor import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_vendors_happy_path():
    csv_text = (
        "name,code,email,payment_terms,accepts_virtual_cards\n"
        "Acme Supplies,ACME,ap@acme.com,Net 30,true\n"
        "Globex,GLBX,billing@globex.com,Net 15,false\n"
    )
    db = _StubSession()
    org_id = uuid.uuid4()

    result = await import_vendors_csv(db, org_id, csv_text)

    assert result.imported == 2
    assert result.skipped == 0
    assert result.errors == []
    assert len(db.added) == 2
    assert db.added[0].name == "Acme Supplies"
    assert db.added[0].accepts_virtual_cards is True
    assert db.added[1].accepts_virtual_cards is False


@pytest.mark.asyncio
async def test_import_vendors_missing_name_is_row_error():
    csv_text = "name,code\n,NONAME\nAcme,ACME\n"
    db = _StubSession()

    result = await import_vendors_csv(db, uuid.uuid4(), csv_text)

    assert result.imported == 1
    assert len(result.errors) == 1
    assert result.errors[0].row == 2  # header is 1, first data row is 2
    assert "name is required" in result.errors[0].message


@pytest.mark.asyncio
async def test_import_vendors_ignores_unknown_columns():
    """Hand us the customer's raw export — we pick the columns we know."""
    csv_text = "name,legacy_id,random_field,email\nAcme,XYZ,garbage,ap@acme.com\n"
    db = _StubSession()

    result = await import_vendors_csv(db, uuid.uuid4(), csv_text)

    assert result.imported == 1
    assert db.added[0].email == "ap@acme.com"


@pytest.mark.asyncio
async def test_import_vendors_handles_empty_file():
    db = _StubSession()

    result = await import_vendors_csv(db, uuid.uuid4(), "name,code\n")

    assert result.imported == 0
    assert result.skipped == 0
    assert result.errors == []


# ---------------------------------------------------------------------------
# Invoice import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_invoices_happy_path():
    csv_text = (
        "invoice_number,vendor_name,amount,invoice_date,due_date,status\n"
        "INV-001,Acme,1000.00,2026-01-15,2026-02-15,done\n"
        'INV-002,Acme,"$2,500.50",2026-02-01,2026-03-01,paid\n'
    )
    db = _StubSession()

    result = await import_invoices_csv(db, uuid.uuid4(), csv_text)

    assert result.imported == 2
    assert result.errors == []
    # 2 invoices + 2 auto-created vendors (stub session says nothing exists)
    from app.models.invoice import Invoice
    from app.models.vendor import Vendor

    added_invoices = [o for o in db.added if isinstance(o, Invoice)]
    added_vendors = [o for o in db.added if isinstance(o, Vendor)]
    assert len(added_invoices) == 2
    assert len(added_vendors) == 2  # Acme created once per row since stub never finds existing
    assert added_invoices[0].amount == Decimal("1000.00")
    assert added_invoices[1].amount == Decimal("2500.50")


@pytest.mark.asyncio
async def test_import_invoices_bad_amount_is_row_error():
    csv_text = "invoice_number,vendor_name,amount\nINV-001,Acme,not-a-number\nINV-002,Acme,100.00\n"
    db = _StubSession()

    result = await import_invoices_csv(db, uuid.uuid4(), csv_text)

    assert result.imported == 1
    assert len(result.errors) == 1
    assert "amount invalid" in result.errors[0].message


@pytest.mark.asyncio
async def test_import_invoices_missing_vendor_identifier_is_row_error():
    csv_text = "invoice_number,vendor_name,vendor_code,amount\nINV-001,,,100.00\n"
    db = _StubSession()

    result = await import_invoices_csv(db, uuid.uuid4(), csv_text)

    assert result.imported == 0
    assert len(result.errors) == 1
    assert "vendor_name or vendor_code" in result.errors[0].message


@pytest.mark.asyncio
async def test_import_invoices_rejects_invalid_status():
    csv_text = "invoice_number,vendor_name,amount,status\nINV-001,Acme,100.00,not-a-status\n"
    db = _StubSession()

    result = await import_invoices_csv(db, uuid.uuid4(), csv_text)

    assert result.imported == 0
    assert len(result.errors) == 1
    assert "status invalid" in result.errors[0].message


@pytest.mark.asyncio
async def test_import_invoices_dedupes_existing_invoice():
    """If stubbed session returns an existing invoice, the row is skipped."""
    from app.models.invoice import Invoice as InvoiceModel

    existing_invoice = MagicMock(spec=InvoiceModel)

    call_count = {"n": 0}

    async def execute_fake(_stmt):
        call_count["n"] += 1
        result = MagicMock()
        # First call(s) = vendor lookups (return None → vendor will be created).
        # Then the invoice dedup lookup returns the existing invoice.
        stmt_str = str(_stmt).lower()
        if "invoice" in stmt_str and "invoice_number" in stmt_str:
            result.scalar_one_or_none = MagicMock(return_value=existing_invoice)
        else:
            result.scalar_one_or_none = MagicMock(return_value=None)
        return result

    db = AsyncMock()
    db.execute = execute_fake
    db.added = []
    db.add = MagicMock(side_effect=lambda o: db.added.append(o))
    db.flush = AsyncMock()

    csv_text = "invoice_number,vendor_name,amount\nINV-001,Acme,100.00\n"
    result = await import_invoices_csv(db, uuid.uuid4(), csv_text)

    assert result.imported == 0
    assert result.skipped == 1
