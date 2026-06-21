"""Serialize a normalized :class:`EInvoiceDocument` to UN/CEFACT CII XML.

This is the exact inverse of :mod:`app.services.e_invoice.cii` — it emits the
same ``rsm:CrossIndustryInvoice`` (D16B) document, with the ``ram:``
(ReusableAggregateBusinessInformationEntity) and ``udt:`` (UnqualifiedDataType)
namespaces, in the element order the parser reads. The strong contract is the
round-trip property::

    parse_cii(generate_cii(doc)) == doc   # on every core field

CII is the dialect Factur-X / ZUGFeRD embed inside a PDF/A-3 — it is NOT UBL.
Header data lives under ``rsm:ExchangedDocument``; the bulk lives under
``rsm:SupplyChainTradeTransaction`` split into three ``ram:`` aggregates:
ApplicableHeaderTradeAgreement (parties + order ref),
ApplicableHeaderTradeDelivery (empty here), and
ApplicableHeaderTradeSettlement (currency, payment, tax, monetary totals).

Built with ``lxml.etree`` (the package's XML engine — no new dependency, no
string templating). etree escapes text nodes automatically, so a vendor name
containing ``<``, ``&``, or ``>`` can never break out of its element or inject
markup. Money is serialized via :func:`Decimal.quantize` and ``str`` — never
``float``. Dates use the CII basic-date form (``format="102"`` → ``YYYYMMDD``)
the parser's :func:`to_date` reads back.

PII note: the generated XML legitimately contains the seller/buyer tax ids,
addresses, and emails — that is the document's purpose. None of those values
ever enters a log line or an error message from this module.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from lxml import etree

from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)

# Namespace URIs — mirror tests/fixtures/e_invoice/cii_invoice.xml exactly.
_NS_RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
_NS_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
_NS_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"

_NSMAP = {"rsm": _NS_RSM, "ram": _NS_RAM, "udt": _NS_UDT}

_DEFAULT_INVOICE_TYPE_CODE = "380"
_DEFAULT_UNIT_CODE = "C62"  # UN/ECE "one (piece)".
_DATE_FORMAT_102 = "102"  # CII basic-date (YYYYMMDD) format qualifier.


def _ram(parent: etree._Element, name: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{_NS_RAM}}}{name}")
    if text is not None:
        el.text = text
    return el


def _udt(parent: etree._Element, name: str, text: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{_NS_UDT}}}{name}")
    el.text = text
    return el


def _amount_text(d: Decimal) -> str:
    """Quantize a monetary amount to 2dp and stringify — never float."""
    return str(d.quantize(Decimal("0.01")))


def _quantity_text(d: Decimal) -> str:
    """Quantize a quantity to 4dp (the InvoiceLineItem column scale)."""
    return str(d.quantize(Decimal("0.0001")))


def _date_102(d: date) -> str:
    """Render a date as the CII basic-date form YYYYMMDD (format 102)."""
    return d.strftime("%Y%m%d")


def _datetime_string(parent: etree._Element, d: date) -> None:
    """Emit ``<udt:DateTimeString format="102">YYYYMMDD</udt:DateTimeString>``."""
    el = _udt(parent, "DateTimeString", _date_102(d))
    el.set("format", _DATE_FORMAT_102)


def _ram_amount(
    parent: etree._Element, name: str, value: Decimal | None, currency: str | None = None
) -> None:
    """Emit a ``ram:`` monetary element; skipped when ``value`` is None so the
    round-trip omits absent optional fields. ``currency`` adds a currencyID
    attribute when supplied (the parser ignores it but the fixture carries one
    on TaxTotalAmount)."""
    if value is None:
        return
    el = _ram(parent, name, _amount_text(value))
    if currency:
        el.set("currencyID", currency)


def _build_party(parent: etree._Element, tag: str, party: EInvoiceParty) -> None:
    """Serialize an :class:`EInvoiceParty` as the inverse of ``cii._parse_party``.

    Element order mirrors the fixture: Name, PostalTradeAddress,
    SpecifiedLegalOrganization, SpecifiedTaxRegistration, URIUniversalCommunication.
    The parser is order-agnostic (it walks children by local name), but emitting
    in document order keeps the output schema-faithful.
    """
    party_el = _ram(parent, tag)

    if party.name:
        _ram(party_el, "Name", party.name)

    # SpecifiedLegalOrganization/ID ← registration_id.
    if party.registration_id:
        legal = _ram(party_el, "SpecifiedLegalOrganization")
        _ram(legal, "ID", party.registration_id)

    has_address = any((party.address_lines, party.city, party.postal_code, party.country_code))
    if has_address:
        addr = _ram(party_el, "PostalTradeAddress")
        # cii._parse_party reads LineOne, LineTwo, LineThree in order into
        # address_lines — emit them back in the same order.
        line_tags = ("LineOne", "LineTwo", "LineThree")
        for tag_name, line in zip(line_tags, party.address_lines, strict=False):
            if line:
                _ram(addr, tag_name, line)
        if party.postal_code:
            _ram(addr, "PostcodeCode", party.postal_code)
        if party.city:
            _ram(addr, "CityName", party.city)
        if party.country_code:
            _ram(addr, "CountryID", party.country_code)

    # SpecifiedTaxRegistration/ID ← tax_id (schemeID="VA", the VAT scheme the
    # fixture uses). The parser reads the first SpecifiedTaxRegistration's ID.
    if party.tax_id:
        reg = _ram(party_el, "SpecifiedTaxRegistration")
        tid = _ram(reg, "ID", party.tax_id)
        tid.set("schemeID", "VA")

    # URIUniversalCommunication/URIID ← email.
    if party.email:
        comm = _ram(party_el, "URIUniversalCommunication")
        _ram(comm, "URIID", party.email)


def _build_line(parent: etree._Element, line: EInvoiceLine, currency: str | None) -> None:
    """ram:IncludedSupplyChainTradeLineItem — the inverse of ``cii._parse_line``."""
    line_el = _ram(parent, "IncludedSupplyChainTradeLineItem")

    if line.line_id:
        doc_line = _ram(line_el, "AssociatedDocumentLineDocument")
        _ram(doc_line, "LineID", line.line_id)

    if line.description or line.item_code:
        product = _ram(line_el, "SpecifiedTradeProduct")
        if line.item_code:
            _ram(product, "SellerAssignedID", line.item_code)
        if line.description:
            _ram(product, "Name", line.description)

    if line.unit_price is not None:
        agreement = _ram(line_el, "SpecifiedLineTradeAgreement")
        net_price = _ram(agreement, "NetPriceProductTradePrice")
        _ram(net_price, "ChargeAmount", _amount_text(line.unit_price))

    if line.quantity is not None:
        delivery = _ram(line_el, "SpecifiedLineTradeDelivery")
        qty = _ram(delivery, "BilledQuantity", _quantity_text(line.quantity))
        qty.set("unitCode", line.unit_code or _DEFAULT_UNIT_CODE)

    if line.tax_rate is not None or line.line_total is not None:
        settlement = _ram(line_el, "SpecifiedLineTradeSettlement")
        if line.tax_rate is not None:
            line_tax = _ram(settlement, "ApplicableTradeTax")
            _ram(line_tax, "RateApplicablePercent", _amount_text(line.tax_rate))
        if line.line_total is not None:
            summation = _ram(settlement, "SpecifiedTradeSettlementLineMonetarySummation")
            _ram(summation, "LineTotalAmount", _amount_text(line.line_total))


def _build_tax(parent: etree._Element, tax: EInvoiceTax) -> None:
    """ram:ApplicableTradeTax — the inverse of ``cii._parse_taxes``. Element
    order mirrors the fixture (CalculatedAmount, CategoryCode, BasisAmount,
    RateApplicablePercent)."""
    tax_el = _ram(parent, "ApplicableTradeTax")
    _ram_amount(tax_el, "CalculatedAmount", tax.tax_amount)
    if tax.category:
        _ram(tax_el, "CategoryCode", tax.category)
    _ram_amount(tax_el, "BasisAmount", tax.taxable_amount)
    if tax.rate is not None:
        _ram(tax_el, "RateApplicablePercent", _amount_text(tax.rate))


def _build_settlement(txn: etree._Element, doc: EInvoiceDocument) -> None:
    """ram:ApplicableHeaderTradeSettlement — currency, payment means, taxes,
    payment terms, and the header monetary summation."""
    settlement = _ram(txn, "ApplicableHeaderTradeSettlement")

    if doc.currency:
        _ram(settlement, "InvoiceCurrencyCode", doc.currency)

    if doc.payment_means_code:
        means = _ram(settlement, "SpecifiedTradeSettlementPaymentMeans")
        _ram(means, "TypeCode", doc.payment_means_code)

    for tax in doc.taxes:
        _build_tax(settlement, tax)

    if doc.payment_terms_note or doc.due_date is not None:
        terms = _ram(settlement, "SpecifiedTradePaymentTerms")
        if doc.payment_terms_note:
            _ram(terms, "Description", doc.payment_terms_note)
        if doc.due_date is not None:
            due = _ram(terms, "DueDateDateTime")
            _datetime_string(due, doc.due_date)

    # Header monetary summation — emit when any total is present.
    has_summation = any(
        v is not None
        for v in (
            doc.line_extension_amount,
            doc.tax_exclusive_amount,
            doc.tax_total,
            doc.allowance_total,
            doc.charge_total,
            doc.tax_inclusive_amount,
            doc.payable_amount,
        )
    )
    if has_summation:
        summation = _ram(settlement, "SpecifiedTradeSettlementHeaderMonetarySummation")
        _ram_amount(summation, "LineTotalAmount", doc.line_extension_amount)
        _ram_amount(summation, "TaxBasisTotalAmount", doc.tax_exclusive_amount)
        _ram_amount(summation, "TaxTotalAmount", doc.tax_total, currency=doc.currency)
        _ram_amount(summation, "AllowanceTotalAmount", doc.allowance_total)
        _ram_amount(summation, "ChargeTotalAmount", doc.charge_total)
        _ram_amount(summation, "GrandTotalAmount", doc.tax_inclusive_amount)
        _ram_amount(summation, "DuePayableAmount", doc.payable_amount)


def generate_cii(doc: EInvoiceDocument) -> bytes:
    """Serialize an :class:`EInvoiceDocument` to UN/CEFACT CII XML bytes.

    Returns UTF-8 bytes with an XML declaration (mirrors ``parse_cii``'s
    ``bytes`` input and feeds ``Response(content=...)`` directly).
    """
    root = etree.Element(f"{{{_NS_RSM}}}CrossIndustryInvoice", nsmap=_NSMAP)

    # rsm:ExchangedDocument — header.
    exchanged = etree.SubElement(root, f"{{{_NS_RSM}}}ExchangedDocument")
    if doc.invoice_number:
        _ram(exchanged, "ID", doc.invoice_number)
    _ram(exchanged, "TypeCode", doc.invoice_type_code or _DEFAULT_INVOICE_TYPE_CODE)
    if doc.issue_date is not None:
        issue = _ram(exchanged, "IssueDateTime")
        _datetime_string(issue, doc.issue_date)

    # rsm:SupplyChainTradeTransaction — line items + three header aggregates.
    txn = etree.SubElement(root, f"{{{_NS_RSM}}}SupplyChainTradeTransaction")

    # Lines first (matches the fixture; the parser finds them at any depth).
    for line in doc.lines:
        _build_line(txn, line, doc.currency)

    # ApplicableHeaderTradeAgreement — buyer reference + parties + order ref.
    agreement = _ram(txn, "ApplicableHeaderTradeAgreement")
    if doc.buyer_reference:
        _ram(agreement, "BuyerReference", doc.buyer_reference)
    _build_party(agreement, "SellerTradeParty", doc.seller)
    _build_party(agreement, "BuyerTradeParty", doc.buyer)
    if doc.order_reference:
        order = _ram(agreement, "BuyerOrderReferencedDocument")
        _ram(order, "IssuerAssignedID", doc.order_reference)

    # ApplicableHeaderTradeDelivery — empty (delivery not mapped this slice),
    # but emitted so the document is structurally faithful to the schema.
    _ram(txn, "ApplicableHeaderTradeDelivery")

    # ApplicableHeaderTradeSettlement.
    _build_settlement(txn, doc)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
