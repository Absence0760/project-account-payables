"""Outbound UN/CEFACT CII generation — the parse(generate(doc)) round-trip.

The strong contract is that ``parse_cii(generate_cii(doc))`` reproduces the
document on every core field. We also pin the exact namespaces / required
elements, money scale + Decimal-not-float, optional-field omission, the
CII basic-date (format 102) form, and XML-escaping of party text.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.e_invoice import generate_cii, parse_cii
from app.services.e_invoice.generate_cii import _NS_RAM, _NS_RSM, _NS_UDT
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)

_CII = (Path(__file__).parent / "fixtures" / "e_invoice" / "cii_invoice.xml").read_bytes()


def _full_doc() -> EInvoiceDocument:
    return EInvoiceDocument(
        source_format=EInvoiceFormat.CII,
        invoice_number="CII-OUT-1",
        issue_date=date(2024, 5, 1),
        due_date=date(2024, 5, 31),
        currency="EUR",
        invoice_type_code="380",
        buyer_reference="REF-7",
        order_reference="PO-123",
        payment_terms_note="Net 30",
        payment_means_code="58",
        seller=EInvoiceParty(
            name="Seller GmbH",
            tax_id="DE123456789",
            registration_id="HRB-12345",
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
                tax_rate=Decimal("19.00"),
            ),
            EInvoiceLine(
                line_id="2",
                item_code="SKU-B",
                description="Widget B",
                quantity=Decimal("4.0000"),
                unit_code="HUR",
                unit_price=Decimal("100.00"),
                line_total=Decimal("400.00"),
            ),
        ],
    )


def test_generate_returns_bytes_with_declaration():
    xml = generate_cii(_full_doc())
    assert isinstance(xml, bytes)
    assert xml.startswith(b"<?xml")


def test_roundtrip_core_header_fields():
    doc = _full_doc()
    back = parse_cii(generate_cii(doc))
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
    back = parse_cii(generate_cii(doc))
    assert back.seller.name == "Seller GmbH"
    assert back.seller.tax_id == "DE123456789"
    assert back.seller.registration_id == "HRB-12345"
    assert back.seller.country_code == "DE"
    assert back.seller.city == "Berlin"
    assert back.seller.postal_code == "10115"
    assert back.buyer.name == "Buyer Ltd"
    assert back.buyer.tax_id == "GB123456789"
    assert back.buyer.country_code == "GB"


def test_roundtrip_multiline_address():
    """LineOne/LineTwo/LineThree must survive intact — a dropped tag or swapped
    order would corrupt a real supplier address."""
    doc = _full_doc()
    doc.seller.address_lines = ["Line 1", "Suite 2", "Building 3"]
    back = parse_cii(generate_cii(doc))
    assert back.seller.address_lines == ["Line 1", "Suite 2", "Building 3"]


def test_roundtrip_email():
    """party.email → ram:URIUniversalCommunication/ram:URIID and back."""
    doc = _full_doc()
    doc.seller.email = "ap@seller.example"
    back = parse_cii(generate_cii(doc))
    assert back.seller.email == "ap@seller.example"


def test_roundtrip_monetary_totals_are_decimal():
    doc = _full_doc()
    back = parse_cii(generate_cii(doc))
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
    back = parse_cii(generate_cii(doc))
    assert len(back.taxes) == 1
    tax = back.taxes[0]
    assert tax.category == "S"
    assert tax.rate == Decimal("19.00")
    assert tax.taxable_amount == Decimal("1000.00")
    assert tax.tax_amount == Decimal("190.00")


def test_roundtrip_lines():
    doc = _full_doc()
    back = parse_cii(generate_cii(doc))
    assert len(back.lines) == 2
    l1, l2 = back.lines
    assert l1.line_id == "1"
    assert l1.item_code == "SKU-A"
    assert l1.description == "Widget A"
    assert l1.quantity == Decimal("10.0000")
    assert l1.unit_code == "C62"
    assert l1.unit_price == Decimal("60.00")
    assert l1.line_total == Decimal("600.00")
    assert l1.tax_rate == Decimal("19.00")
    assert isinstance(l1.quantity, Decimal)
    assert isinstance(l1.unit_price, Decimal)
    assert l2.line_id == "2"
    assert l2.unit_code == "HUR"
    assert l2.unit_price == Decimal("100.00")
    assert l2.line_total == Decimal("400.00")


def test_namespaces_and_root_match_fixture():
    """The generated root + namespaces must equal the inbound CII fixture's."""
    from lxml import etree

    xml = generate_cii(_full_doc())
    root = etree.fromstring(xml)
    assert etree.QName(root).namespace == _NS_RSM
    assert etree.QName(root).localname == "CrossIndustryInvoice"
    nsmap = root.nsmap
    assert nsmap["rsm"] == _NS_RSM
    assert nsmap["ram"] == _NS_RAM
    assert nsmap["udt"] == _NS_UDT


def test_required_structural_elements_present():
    """Golden-ish structural assertion — the spine the CII schema requires must
    be present: ExchangedDocument header, the three trade-transaction aggregates,
    a line item, and the header monetary summation."""
    from lxml import etree

    root = etree.fromstring(generate_cii(_full_doc()))

    def has(local: str) -> bool:
        return any(etree.QName(el).localname == local for el in root.iter())

    for required in (
        "ExchangedDocument",
        "SupplyChainTradeTransaction",
        "IncludedSupplyChainTradeLineItem",
        "ApplicableHeaderTradeAgreement",
        "SellerTradeParty",
        "BuyerTradeParty",
        "ApplicableHeaderTradeDelivery",
        "ApplicableHeaderTradeSettlement",
        "SpecifiedTradeSettlementHeaderMonetarySummation",
        "DuePayableAmount",
    ):
        assert has(required), f"missing required CII element {required}"


def test_issue_date_uses_basic_date_format_102():
    """Dates emit as the CII basic-date form YYYYMMDD with format='102', the
    qualifier the parser's to_date reads back."""
    from lxml import etree

    root = etree.fromstring(generate_cii(_full_doc()))
    issue = root.find(
        f"{{{_NS_RSM}}}ExchangedDocument/{{{_NS_RAM}}}IssueDateTime/{{{_NS_UDT}}}DateTimeString"
    )
    assert issue is not None
    assert issue.get("format") == "102"
    assert issue.text == "20240501"


def test_tax_total_carries_currency_id():
    from lxml import etree

    root = etree.fromstring(generate_cii(_full_doc()))
    tax_total = root.find(
        f".//{{{_NS_RAM}}}SpecifiedTradeSettlementHeaderMonetarySummation"
        f"/{{{_NS_RAM}}}TaxTotalAmount"
    )
    assert tax_total is not None
    assert tax_total.get("currencyID") == "EUR"
    assert tax_total.text == "190.00"


def test_money_scale_2dp_quantity_4dp():
    """A 3dp tax_rate and over-precise quantity quantize to the column scale."""
    doc = _full_doc()
    doc.taxes[0].rate = Decimal("19.999")
    doc.lines[0].quantity = Decimal("10.00018")
    back = parse_cii(generate_cii(doc))
    assert back.taxes[0].rate == Decimal("20.00")
    assert back.lines[0].quantity == Decimal("10.0002")


def test_full_fixture_roundtrip():
    """Parse the inbound CII fixture, regenerate, reparse — core fields survive."""
    original = parse_cii(_CII)
    back = parse_cii(generate_cii(original))
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
    doc.payment_terms_note = None
    doc.taxes = []
    doc.tax_total = None
    for line in doc.lines:
        line.tax_rate = None
    xml = generate_cii(doc)
    assert b"DueDateDateTime" not in xml
    assert b"BuyerOrderReferencedDocument" not in xml
    assert b"SpecifiedTradePaymentTerms" not in xml
    back = parse_cii(xml)
    assert back.due_date is None
    assert back.order_reference is None
    assert back.payment_terms_note is None
    assert back.taxes == []
    # tax_total falls back to None when no summation TaxTotalAmount is emitted.
    assert back.tax_total is None


def test_xml_escaping_of_party_name():
    """A vendor name with <, &, > round-trips intact — proves text nodes are
    escaped (etree), not string-templated."""
    doc = _full_doc()
    doc.seller.name = "Acme <Manufacturing> & Co."
    xml = generate_cii(doc)
    assert b"<Manufacturing>" not in xml.replace(b"&lt;Manufacturing&gt;", b"")
    back = parse_cii(xml)
    assert back.seller.name == "Acme <Manufacturing> & Co."


def test_no_float_in_amount_text():
    """Over-precise input is quantized via Decimal, not float."""
    doc = _full_doc()
    doc.payable_amount = Decimal("100.005")
    doc.tax_inclusive_amount = Decimal("100.005")
    back = parse_cii(generate_cii(doc))
    # Decimal ROUND_HALF_EVEN: 100.005 -> 100.00.
    assert back.payable_amount == Decimal("100.00")
    assert isinstance(back.payable_amount, Decimal)
