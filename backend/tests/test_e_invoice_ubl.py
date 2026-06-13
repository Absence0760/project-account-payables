"""Parse a UBL 2.1 Invoice fixture and assert the normalized mapping."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.e_invoice import parse_ubl
from app.services.e_invoice.model import EInvoiceFormat

_UBL = (Path(__file__).parent / "fixtures" / "e_invoice" / "ubl_invoice.xml").read_bytes()


def test_ubl_header_fields():
    doc = parse_ubl(_UBL)
    assert doc.source_format is EInvoiceFormat.UBL
    assert doc.invoice_number == "INV-2024-0042"
    assert doc.issue_date == date(2024, 3, 15)
    assert doc.due_date == date(2024, 4, 14)
    assert doc.currency == "EUR"
    assert doc.invoice_type_code == "380"
    assert doc.buyer_reference == "BUYER-REF-99"
    assert doc.order_reference == "PO-7788"
    assert doc.payment_means_code == "30"
    assert doc.payment_terms_note == "Net 30 days"


def test_ubl_parties():
    doc = parse_ubl(_UBL)
    assert doc.seller.name == "Seller GmbH"
    assert doc.seller.tax_id == "DE123456789"
    assert doc.seller.country_code == "DE"
    assert doc.seller.city == "Berlin"
    assert doc.buyer.name == "Buyer Ltd"
    assert doc.buyer.country_code == "GB"


def test_ubl_totals_are_decimal():
    doc = parse_ubl(_UBL)
    assert doc.line_extension_amount == Decimal("1000.00")
    assert doc.tax_exclusive_amount == Decimal("1000.00")
    assert doc.tax_inclusive_amount == Decimal("1190.00")
    assert doc.tax_total == Decimal("190.00")
    assert doc.payable_amount == Decimal("1190.00")
    for v in (
        doc.line_extension_amount,
        doc.tax_inclusive_amount,
        doc.tax_total,
        doc.payable_amount,
    ):
        assert isinstance(v, Decimal)


def test_ubl_tax_subtotals():
    doc = parse_ubl(_UBL)
    assert len(doc.taxes) == 1
    tax = doc.taxes[0]
    assert tax.category == "S"
    assert tax.rate == Decimal("19.00")
    assert tax.taxable_amount == Decimal("1000.00")
    assert tax.tax_amount == Decimal("190.00")


def test_ubl_lines():
    doc = parse_ubl(_UBL)
    assert len(doc.lines) == 2
    line1, line2 = doc.lines
    assert line1.line_id == "1"
    assert line1.description == "Widget A"
    assert line1.item_code == "SKU-A"
    assert line1.quantity == Decimal("10")
    assert line1.unit_code == "C62"
    assert line1.unit_price == Decimal("60.00")
    assert line1.line_total == Decimal("600.00")
    assert line1.tax_amount == Decimal("114.00")
    assert line1.tax_rate == Decimal("19.00")
    assert isinstance(line1.quantity, Decimal)
    assert isinstance(line1.unit_price, Decimal)
    assert isinstance(line1.line_total, Decimal)

    assert line2.description == "Widget B"
    assert line2.unit_price == Decimal("100.00")
    assert line2.line_total == Decimal("400.00")
