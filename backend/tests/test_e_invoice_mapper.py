"""invoice_to_einvoice_document — ORM Invoice → normalized EInvoiceDocument.

Pins the field-by-field mapping per the spec's documentModelReuse, the buyer
filled from BuyerIdentity, vendor_address split into address_lines, and Decimal
amounts preserved exactly (never coerced to float).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.e_invoice import BuyerIdentity, invoice_to_einvoice_document
from app.services.e_invoice.model import EInvoiceFormat


def _invoice() -> SimpleNamespace:
    """A SimpleNamespace standing in for an ORM Invoice — the mapper only reads
    attributes, never touches the session."""
    return SimpleNamespace(
        invoice_number="INV-77",
        invoice_date=date(2024, 6, 1),
        due_date=date(2024, 7, 1),
        currency="EUR",
        reference_number="BUYER-REF",
        po_number="PO-9000",
        payment_terms="Net 30",
        payment_method="ach",
        vendor_name="Vendor SARL",
        vendor_tax_id="FR40123456789",
        vendor_address="12 Rue de Paris\nSuite 4\n75001 Paris",
        subtotal=Decimal("1000.00"),
        amount=Decimal("1200.00"),
        tax_amount=Decimal("200.00"),
        tax_rate=Decimal("20.00"),
        discount_amount=Decimal("0.00"),
        shipping_amount=Decimal("0.00"),
    )


def _line(**kw) -> SimpleNamespace:
    base = dict(
        line_number=1,
        item_code="SKU-1",
        description="Service",
        quantity=Decimal("2.0000"),
        unit_price=Decimal("500.00"),
        total=Decimal("1000.00"),
        tax=Decimal("200.00"),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _buyer() -> BuyerIdentity:
    return BuyerIdentity(
        name="Our Company Inc",
        tax_id="DE999999999",
        address_lines=["1 Main St"],
        city="Berlin",
        postal_code="10115",
        country_code="DE",
        email="ap@ourco.test",
    )


def test_maps_header_fields():
    doc = invoice_to_einvoice_document(_invoice(), [_line()], _buyer())
    assert doc.source_format is EInvoiceFormat.UBL
    assert doc.invoice_number == "INV-77"
    assert doc.issue_date == date(2024, 6, 1)
    assert doc.due_date == date(2024, 7, 1)
    assert doc.currency == "EUR"
    assert doc.invoice_type_code == "380"
    assert doc.buyer_reference == "BUYER-REF"
    assert doc.order_reference == "PO-9000"
    assert doc.payment_terms_note == "Net 30"
    assert doc.payment_means_code == "ach"


def test_seller_is_the_vendor():
    doc = invoice_to_einvoice_document(_invoice(), [_line()], _buyer())
    assert doc.seller.name == "Vendor SARL"
    assert doc.seller.tax_id == "FR40123456789"
    assert doc.seller.address_lines == ["12 Rue de Paris", "Suite 4", "75001 Paris"]


def test_buyer_is_filled_from_buyer_identity():
    doc = invoice_to_einvoice_document(_invoice(), [_line()], _buyer())
    assert doc.buyer.name == "Our Company Inc"
    assert doc.buyer.tax_id == "DE999999999"
    assert doc.buyer.address_lines == ["1 Main St"]
    assert doc.buyer.city == "Berlin"
    assert doc.buyer.postal_code == "10115"
    assert doc.buyer.country_code == "DE"
    assert doc.buyer.email == "ap@ourco.test"


def test_monetary_totals_mapping_and_decimal():
    doc = invoice_to_einvoice_document(_invoice(), [_line()], _buyer())
    assert doc.line_extension_amount == Decimal("1000.00")
    assert doc.tax_exclusive_amount == Decimal("1000.00")
    assert doc.tax_inclusive_amount == Decimal("1200.00")
    assert doc.tax_total == Decimal("200.00")
    assert doc.allowance_total == Decimal("0.00")
    assert doc.charge_total == Decimal("0.00")
    assert doc.payable_amount == Decimal("1200.00")
    for v in (
        doc.line_extension_amount,
        doc.tax_inclusive_amount,
        doc.tax_total,
        doc.payable_amount,
    ):
        assert isinstance(v, Decimal)


def test_tax_built_from_invoice_when_present():
    doc = invoice_to_einvoice_document(_invoice(), [_line()], _buyer())
    assert len(doc.taxes) == 1
    tax = doc.taxes[0]
    assert tax.rate == Decimal("20.00")
    assert tax.taxable_amount == Decimal("1000.00")
    assert tax.tax_amount == Decimal("200.00")


def test_no_tax_when_invoice_has_no_tax():
    inv = _invoice()
    inv.tax_amount = None
    inv.tax_rate = None
    doc = invoice_to_einvoice_document(inv, [_line()], _buyer())
    assert doc.taxes == []


def test_lines_mapping():
    doc = invoice_to_einvoice_document(_invoice(), [_line(line_number=3)], _buyer())
    assert len(doc.lines) == 1
    line = doc.lines[0]
    assert line.line_id == "3"
    assert line.item_code == "SKU-1"
    assert line.description == "Service"
    assert line.quantity == Decimal("2.0000")
    assert line.unit_price == Decimal("500.00")
    assert line.line_total == Decimal("1000.00")
    assert line.tax_amount == Decimal("200.00")
    assert isinstance(line.quantity, Decimal)
    assert isinstance(line.unit_price, Decimal)


def test_empty_vendor_address_yields_no_lines():
    inv = _invoice()
    inv.vendor_address = None
    doc = invoice_to_einvoice_document(inv, [_line()], _buyer())
    assert doc.seller.address_lines == []


def test_seller_country_derived_from_vat_prefix():
    """The Invoice row has no vendor-country column, so the mapper derives the
    seller country from the VAT-id prefix (FR40123456789 → FR). This is what
    makes the outbound export guard validate the supplier's tax id + rate,
    not just the buyer side."""
    doc = invoice_to_einvoice_document(_invoice(), [_line()], _buyer())
    assert doc.seller.country_code == "FR"


def test_seller_country_greece_el_prefix_maps_to_gr():
    """Greece is the one EU member whose VAT prefix (EL) differs from its ISO-2
    code (GR) — the mapper must translate it."""
    inv = _invoice()
    inv.vendor_tax_id = "EL123456789"
    doc = invoice_to_einvoice_document(inv, [_line()], _buyer())
    assert doc.seller.country_code == "GR"


def test_seller_country_none_for_unrecognised_prefix():
    """A non-VAT-prefixed scheme (US EIN, AU ABN, bare number) leaves the seller
    country None — the tax-rule validators skip a None country, so this is safe
    (same behaviour as before the country derivation)."""
    inv = _invoice()
    inv.vendor_tax_id = "12-3456789"  # US EIN — no recognised prefix
    doc = invoice_to_einvoice_document(inv, [_line()], _buyer())
    assert doc.seller.country_code is None

    inv.vendor_tax_id = None
    doc = invoice_to_einvoice_document(inv, [_line()], _buyer())
    assert doc.seller.country_code is None
