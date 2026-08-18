"""Parse a UN/CEFACT CII fixture (the dialect Factur-X embeds)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.e_invoice import parse_cii
from app.services.e_invoice.model import EInvoiceFormat

_CII = (Path(__file__).parent / "fixtures" / "e_invoice" / "cii_invoice.xml").read_bytes()


def test_cii_header_fields():
    doc = parse_cii(_CII)
    assert doc.source_format is EInvoiceFormat.CII
    assert doc.invoice_number == "CII-2024-7"
    assert doc.invoice_type_code == "380"
    # udt:DateTimeString format="102" → YYYYMMDD basic date.
    assert doc.issue_date == date(2024, 5, 22)
    assert doc.due_date == date(2024, 6, 5)
    assert doc.currency == "EUR"
    assert doc.buyer_reference == "PROJ-ALPHA"
    assert doc.order_reference == "PO-CII-555"
    assert doc.payment_means_code == "58"
    assert doc.payment_terms_note == "Payable within 14 days"


def test_cii_parties():
    doc = parse_cii(_CII)
    assert doc.seller.name == "Acme Consulting SARL"
    assert doc.seller.tax_id == "FR40123456789"
    assert doc.seller.country_code == "FR"
    assert doc.seller.city == "Paris"
    assert doc.buyer.name == "Globex Buyer Inc"
    assert doc.buyer.country_code == "US"


def test_cii_totals_are_decimal():
    doc = parse_cii(_CII)
    assert doc.line_extension_amount == Decimal("1200.00")
    assert doc.tax_exclusive_amount == Decimal("1200.00")
    assert doc.tax_total == Decimal("240.00")
    assert doc.tax_inclusive_amount == Decimal("1440.00")  # GrandTotalAmount
    assert doc.payable_amount == Decimal("1440.00")  # DuePayableAmount
    for v in (
        doc.line_extension_amount,
        doc.tax_total,
        doc.tax_inclusive_amount,
        doc.payable_amount,
    ):
        assert isinstance(v, Decimal)


def test_cii_tax():
    doc = parse_cii(_CII)
    assert len(doc.taxes) == 1
    tax = doc.taxes[0]
    assert tax.category == "S"
    assert tax.rate == Decimal("20.00")
    assert tax.taxable_amount == Decimal("1200.00")
    assert tax.tax_amount == Decimal("240.00")


def test_cii_lines():
    doc = parse_cii(_CII)
    assert len(doc.lines) == 1
    line = doc.lines[0]
    assert line.line_id == "1"
    assert line.description == "Consulting hours"
    assert line.item_code == "ART-100"
    assert line.quantity == Decimal("8")
    assert line.unit_code == "HUR"
    assert line.unit_price == Decimal("150.00")
    assert line.line_total == Decimal("1200.00")
    assert line.tax_rate == Decimal("20.00")
    assert isinstance(line.quantity, Decimal)
    assert isinstance(line.line_total, Decimal)


def test_cii_line_tax_amount_is_read():
    """Regression: `ram:ApplicableTradeTax/ram:CalculatedAmount` on a LINE.

    The UBL parser has always read the per-line tax figure
    (`cac:TaxTotal/cbc:TaxAmount`); the CII parser did not, so every Factur-X /
    ZUGFeRD line arrived with `tax_amount=None` and the `einvoice` extraction
    adapter — which maps `line.tax_amount` onto `ExtractedLineItem.tax` — wrote
    a NULL tax onto every `InvoiceLineItem` of a document that stated one.

    The shipped fixture's line carries only a rate, so this asserts against a
    line-level `CalculatedAmount` explicitly.
    """
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument><ram:ID>CII-TAX-1</ram:ID></rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument>
        <ram:LineID>1</ram:LineID>
      </ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedLineTradeSettlement>
        <ram:ApplicableTradeTax>
          <ram:CalculatedAmount>240.00</ram:CalculatedAmount>
          <ram:RateApplicablePercent>20.00</ram:RateApplicablePercent>
        </ram:ApplicableTradeTax>
        <ram:SpecifiedTradeSettlementLineMonetarySummation>
          <ram:LineTotalAmount>1200.00</ram:LineTotalAmount>
        </ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""
    line = parse_cii(xml).lines[0]
    assert line.tax_amount == Decimal("240.00")
    assert isinstance(line.tax_amount, Decimal)
    assert line.tax_rate == Decimal("20.00")
