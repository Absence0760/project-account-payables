"""Outbound UBL 2.1 generation — the parse(generate(doc)) round-trip property.

The strong contract is that ``parse_ubl(generate_ubl(doc))`` reproduces the
document on every core field. We also pin the exact namespaces/elements,
money scale + currencyID, Decimal-not-float, optional-field omission, and
XML-escaping of party text.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.e_invoice import generate_ubl, parse_ubl
from app.services.e_invoice.generate import _NS_CAC, _NS_CBC, _NS_INVOICE
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)

_UBL = (Path(__file__).parent / "fixtures" / "e_invoice" / "ubl_invoice.xml").read_bytes()


def _full_doc() -> EInvoiceDocument:
    return EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number="INV-OUT-1",
        issue_date=date(2024, 5, 1),
        due_date=date(2024, 5, 31),
        currency="EUR",
        invoice_type_code="380",
        buyer_reference="REF-7",
        order_reference="PO-123",
        payment_terms_note="Net 30",
        payment_means_code="30",
        seller=EInvoiceParty(
            name="Seller GmbH",
            tax_id="DE123456789",
            address_lines=["Hauptstrasse 1"],
            city="Berlin",
            postal_code="10115",
            country_code="DE",
        ),
        buyer=EInvoiceParty(
            name="Buyer Ltd",
            tax_id="GB123456789",
            country_code="GB",
        ),
        line_extension_amount=Decimal("1000.00"),
        tax_exclusive_amount=Decimal("1000.00"),
        tax_inclusive_amount=Decimal("1190.00"),
        tax_total=Decimal("190.00"),
        allowance_total=Decimal("0.00"),
        charge_total=Decimal("0.00"),
        payable_amount=Decimal("1190.00"),
        taxes=[
            EInvoiceTax(
                category="S",
                rate=Decimal("19.00"),
                taxable_amount=Decimal("1000.00"),
                tax_amount=Decimal("190.00"),
            )
        ],
        lines=[
            EInvoiceLine(
                line_id="1",
                item_code="SKU-A",
                description="Widget A",
                quantity=Decimal("10.0000"),
                unit_code="C62",
                unit_price=Decimal("60.00"),
                line_total=Decimal("600.00"),
                tax_amount=Decimal("114.00"),
                tax_rate=Decimal("19.00"),
            ),
            EInvoiceLine(
                line_id="2",
                item_code="SKU-B",
                description="Widget B",
                quantity=Decimal("4.0000"),
                unit_code="C62",
                unit_price=Decimal("100.00"),
                line_total=Decimal("400.00"),
            ),
        ],
    )


def test_generate_returns_bytes_with_declaration():
    xml = generate_ubl(_full_doc())
    assert isinstance(xml, bytes)
    assert xml.startswith(b"<?xml")


def test_roundtrip_core_header_fields():
    doc = _full_doc()
    back = parse_ubl(generate_ubl(doc))
    assert back.invoice_number == doc.invoice_number
    assert back.issue_date == doc.issue_date
    assert back.due_date == doc.due_date
    assert back.currency == doc.currency
    assert back.invoice_type_code == "380"
    assert back.buyer_reference == doc.buyer_reference
    assert back.order_reference == doc.order_reference
    assert back.payment_terms_note == doc.payment_terms_note
    assert back.payment_means_code == doc.payment_means_code


def test_roundtrip_parties():
    doc = _full_doc()
    back = parse_ubl(generate_ubl(doc))
    assert back.seller.name == "Seller GmbH"
    assert back.seller.tax_id == "DE123456789"
    assert back.seller.country_code == "DE"
    assert back.seller.city == "Berlin"
    assert back.seller.postal_code == "10115"
    assert back.buyer.name == "Buyer Ltd"
    assert back.buyer.tax_id == "GB123456789"
    assert back.buyer.country_code == "GB"


def test_roundtrip_monetary_totals_are_decimal():
    doc = _full_doc()
    back = parse_ubl(generate_ubl(doc))
    assert back.line_extension_amount == Decimal("1000.00")
    assert back.tax_exclusive_amount == Decimal("1000.00")
    assert back.tax_inclusive_amount == Decimal("1190.00")
    assert back.tax_total == Decimal("190.00")
    assert back.allowance_total == Decimal("0.00")
    assert back.charge_total == Decimal("0.00")
    assert back.payable_amount == Decimal("1190.00")
    for v in (
        back.line_extension_amount,
        back.tax_inclusive_amount,
        back.tax_total,
        back.payable_amount,
    ):
        assert isinstance(v, Decimal)


def test_roundtrip_tax_subtotal():
    doc = _full_doc()
    back = parse_ubl(generate_ubl(doc))
    assert len(back.taxes) == 1
    tax = back.taxes[0]
    assert tax.category == "S"
    assert tax.rate == Decimal("19.00")
    assert tax.taxable_amount == Decimal("1000.00")
    assert tax.tax_amount == Decimal("190.00")


def test_roundtrip_lines():
    doc = _full_doc()
    back = parse_ubl(generate_ubl(doc))
    assert len(back.lines) == 2
    l1, l2 = back.lines
    assert l1.line_id == "1"
    assert l1.item_code == "SKU-A"
    assert l1.description == "Widget A"
    assert l1.quantity == Decimal("10.0000")
    assert l1.unit_code == "C62"
    assert l1.unit_price == Decimal("60.00")
    assert l1.line_total == Decimal("600.00")
    assert l1.tax_amount == Decimal("114.00")
    assert l1.tax_rate == Decimal("19.00")
    assert isinstance(l1.quantity, Decimal)
    assert isinstance(l1.unit_price, Decimal)
    assert l2.line_id == "2"
    assert l2.unit_price == Decimal("100.00")
    assert l2.line_total == Decimal("400.00")


def test_namespaces_match_fixture():
    """The generated root + namespaces must equal the inbound fixture's."""
    from lxml import etree

    xml = generate_ubl(_full_doc())
    root = etree.fromstring(xml)
    assert etree.QName(root).namespace == _NS_INVOICE
    assert etree.QName(root).localname == "Invoice"
    nsmap = root.nsmap
    assert nsmap["cac"] == _NS_CAC
    assert nsmap["cbc"] == _NS_CBC


def test_amount_elements_carry_currency_id():
    from lxml import etree

    xml = generate_ubl(_full_doc())
    root = etree.fromstring(xml)
    payable = root.find(f"{{{_NS_CAC}}}LegalMonetaryTotal/{{{_NS_CBC}}}PayableAmount")
    assert payable is not None
    assert payable.get("currencyID") == "EUR"
    assert payable.text == "1190.00"


def test_money_scale_2dp_quantity_4dp():
    """A 3dp tax_rate and over-precise amount quantize to the column scale."""
    doc = _full_doc()
    doc.taxes[0].rate = Decimal("19.999")
    doc.lines[0].quantity = Decimal("10.00018")
    back = parse_ubl(generate_ubl(doc))
    # rate quantizes to 2dp (19.999 -> 20.00).
    assert back.taxes[0].rate == Decimal("20.00")
    # quantity quantizes to 4dp (10.00018 -> 10.0002).
    assert back.lines[0].quantity == Decimal("10.0002")


def test_full_fixture_roundtrip():
    """parse the inbound fixture, regenerate, reparse — core fields survive."""
    original = parse_ubl(_UBL)
    back = parse_ubl(generate_ubl(original))
    assert back.invoice_number == original.invoice_number
    assert back.currency == original.currency
    assert back.seller.name == original.seller.name
    assert back.seller.tax_id == original.seller.tax_id
    assert back.payable_amount == original.payable_amount
    assert len(back.lines) == len(original.lines)
    assert back.lines[0].line_total == original.lines[0].line_total


def test_optional_fields_omitted_roundtrip_to_none():
    """A doc without due_date / taxes / order_reference must not emit those
    elements and must round-trip back to the same Nones (no empty strings)."""
    doc = _full_doc()
    doc.due_date = None
    doc.order_reference = None
    doc.taxes = []
    doc.tax_total = None
    # Also clear line-level tax so no cac:TaxTotal is emitted anywhere.
    for line in doc.lines:
        line.tax_amount = None
        line.tax_rate = None
    xml = generate_ubl(doc)
    assert b"DueDate" not in xml
    assert b"OrderReference" not in xml
    assert b"TaxTotal" not in xml
    back = parse_ubl(xml)
    assert back.due_date is None
    assert back.order_reference is None
    assert back.taxes == []
    assert back.tax_total is None


def test_xml_escaping_of_party_name():
    """A vendor name with <, &, > round-trips intact — proves text nodes are
    escaped (etree), not string-templated."""
    doc = _full_doc()
    doc.seller.name = "Acme <Manufacturing> & Co."
    xml = generate_ubl(doc)
    # The raw bytes must contain escaped entities, never the literal markup.
    assert b"<Manufacturing>" not in xml.replace(b"&lt;Manufacturing&gt;", b"")
    back = parse_ubl(xml)
    assert back.seller.name == "Acme <Manufacturing> & Co."


def test_no_float_in_amount_text():
    """Over-precise binary-float-style input is quantized via Decimal, not
    float — feeding Decimal('100.005') yields a clean 2dp string."""
    doc = _full_doc()
    doc.payable_amount = Decimal("100.005")
    doc.tax_inclusive_amount = Decimal("100.005")
    back = parse_ubl(generate_ubl(doc))
    # Decimal ROUND_HALF_EVEN: 100.005 -> 100.00.
    assert back.payable_amount == Decimal("100.00")
    assert isinstance(back.payable_amount, Decimal)
