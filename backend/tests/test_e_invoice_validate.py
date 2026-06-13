"""Structural validation: field-level errors, no PII in messages."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from app.services.e_invoice import EInvoiceValidationError, validate_document
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
)
from app.services.e_invoice.parse import parse_e_invoice
from app.services.e_invoice.validate import assert_valid


def _valid_doc() -> EInvoiceDocument:
    return EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number="INV-1",
        issue_date=date(2024, 1, 1),
        currency="USD",
        seller=EInvoiceParty(name="Seller Inc", tax_id="US-99-1234567"),
        buyer=EInvoiceParty(name="Buyer Inc"),
        line_extension_amount=Decimal("100.00"),
        tax_exclusive_amount=Decimal("100.00"),
        tax_total=Decimal("10.00"),
        tax_inclusive_amount=Decimal("110.00"),
        payable_amount=Decimal("110.00"),
        lines=[EInvoiceLine(line_id="1", description="Item", line_total=Decimal("100.00"))],
    )


def _codes_for(doc) -> dict[str, str]:
    return {e.field: e.code for e in validate_document(doc)}


def test_valid_document_has_no_errors():
    assert validate_document(_valid_doc()) == []


def test_missing_invoice_number():
    doc = _valid_doc()
    doc.invoice_number = None
    assert _codes_for(doc).get("invoice_number") == "missing"


def test_missing_seller_name():
    doc = _valid_doc()
    doc.seller = EInvoiceParty(name=None)
    assert _codes_for(doc).get("seller.name") == "missing"


def test_missing_buyer_name():
    doc = _valid_doc()
    doc.buyer = EInvoiceParty(name=None)
    assert _codes_for(doc).get("buyer.name") == "missing"


def test_no_lines():
    doc = _valid_doc()
    doc.lines = []
    assert _codes_for(doc).get("lines") == "missing"


def test_bad_currency():
    doc = _valid_doc()
    doc.currency = "EURO"
    assert _codes_for(doc).get("currency") == "malformed"


def test_missing_grand_total():
    doc = _valid_doc()
    doc.payable_amount = None
    doc.tax_inclusive_amount = None
    assert _codes_for(doc).get("payable_amount") == "missing"


def test_total_mismatch_is_inconsistent():
    doc = _valid_doc()
    # tax_inclusive (110) != tax_exclusive (100) + tax_total (5).
    doc.tax_total = Decimal("5.00")
    assert _codes_for(doc).get("tax_inclusive_amount") == "inconsistent"


def test_small_rounding_within_tolerance_passes():
    doc = _valid_doc()
    doc.tax_inclusive_amount = Decimal("110.01")  # 0.01 over — within tolerance
    assert "tax_inclusive_amount" not in _codes_for(doc)


def test_total_mismatch_just_over_tolerance_fails():
    """The other side of the rounding boundary: a 0.02 discrepancy (just over
    the 0.01 tolerance) must fire the inconsistent error."""
    doc = _valid_doc()
    doc.tax_exclusive_amount = Decimal("100.00")
    doc.tax_total = Decimal("10.00")
    doc.tax_inclusive_amount = Decimal("110.02")  # 0.02 over — outside tolerance
    assert _codes_for(doc).get("tax_inclusive_amount") == "inconsistent"


def test_assert_valid_raises_with_error_list():
    doc = _valid_doc()
    doc.invoice_number = None
    doc.seller = EInvoiceParty(name=None)
    with pytest.raises(EInvoiceValidationError) as exc_info:
        assert_valid(doc)
    fields = {e.field for e in exc_info.value.errors}
    assert "invoice_number" in fields
    assert "seller.name" in fields


def test_error_messages_contain_no_pii_values():
    """Critical: FieldError.message must name the FIELD, never the value —
    no tax id, address, or amount may leak into a log line or error body."""
    doc = EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number=None,
        issue_date=None,
        currency="EURO",  # malformed
        seller=EInvoiceParty(
            name=None,
            tax_id="DE-SECRET-123456789",
            address_lines=["1 Secret Lane", "Confidential City"],
        ),
        buyer=EInvoiceParty(name=None),
        tax_exclusive_amount=Decimal("100.00"),
        tax_total=Decimal("99.99"),
        tax_inclusive_amount=Decimal("110.00"),  # inconsistent
        payable_amount=Decimal("110.00"),
        lines=[],
    )
    errors = validate_document(doc)
    assert errors  # there should be several
    forbidden = [
        "DE-SECRET-123456789",
        "Secret Lane",
        "Confidential City",
        "110.00",
        "99.99",
        "100.00",
        "EURO",
    ]
    for err in errors:
        for needle in forbidden:
            assert needle not in err.message, f"PII/value leaked in message: {err.message!r}"
            assert needle not in err.field


def test_error_str_is_pii_free_join():
    doc = _valid_doc()
    doc.invoice_number = None
    exc = EInvoiceValidationError(validate_document(doc))
    assert str(exc) == "invoice_number: missing"


def test_fielderror_is_a_dataclass():
    # Stable public shape for callers building 'field: code' strings.
    errors = validate_document(EInvoiceDocument(source_format=EInvoiceFormat.UBL))
    assert all(dataclasses.is_dataclass(e) for e in errors)


def test_parse_e_invoice_translates_syntactically_broken_xml_to_malformed():
    """The except-XMLSyntaxError translate branch in parse.py must surface a
    field-named 'malformed' EInvoiceValidationError — never an unhandled
    lxml.etree.XMLSyntaxError that would 500 the worker.

    detect_format itself parses the bytes to classify the root, so a raw
    broken-XML file is caught there and returns NONE (→ ValueError, not this
    branch). The branch is genuinely reached via the Factur-X path: the
    embedded-file extractor matches the conventional 'factur-x.xml' attachment
    name *without* validating its content, so a hybrid PDF can carry
    syntactically broken CII. detect → FACTURX_PDF, then parse_cii on the
    broken bytes raises XMLSyntaxError, which parse.py translates."""
    import fitz

    broken_cii = b'<?xml version="1.0"?><rsm:CrossIndustryInvoice><Unclosed'
    pdf = fitz.open()
    pdf.new_page()
    pdf.embfile_add("factur-x.xml", broken_cii, filename="factur-x.xml")
    pdf_bytes = pdf.tobytes()

    with pytest.raises(EInvoiceValidationError) as exc_info:
        parse_e_invoice(pdf_bytes, mime_type="application/pdf", filename="bad.pdf")
    assert exc_info.value.errors[0].code == "malformed"
    assert exc_info.value.errors[0].field == "document"
