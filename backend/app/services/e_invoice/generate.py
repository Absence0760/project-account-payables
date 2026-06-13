"""Serialize a normalized :class:`EInvoiceDocument` to UBL 2.1 Invoice XML.

This is the exact inverse of :mod:`app.services.e_invoice.ubl` — it emits the
same ``urn:oasis:names:specification:ubl:schema:xsd:Invoice-2`` document, with
the ``cbc:`` (CommonBasicComponents) and ``cac:`` (CommonAggregateComponents)
namespaces, in the element order the parser reads. The strong contract is the
round-trip property::

    parse_ubl(generate_ubl(doc)) == doc   # on every core field

Built with ``lxml.etree`` (the package's XML engine — no new dependency, no
string templating). etree escapes text nodes automatically, so a vendor name
containing ``<``, ``&``, or ``>`` can never break out of its element or inject
markup. Money is serialized via :func:`Decimal.quantize` and ``str`` — never
``float`` — and every monetary element carries the ``currencyID`` attribute,
matching the inbound fixture.

PII note: the generated XML legitimately contains the seller/buyer tax ids,
addresses, and emails — that is the document's purpose. None of those values
ever enters a log line or an error message from this module.
"""

from __future__ import annotations

from decimal import Decimal

from lxml import etree

from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)

# Namespace URIs — mirror tests/fixtures/e_invoice/ubl_invoice.xml exactly.
_NS_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
_NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

_NSMAP = {None: _NS_INVOICE, "cac": _NS_CAC, "cbc": _NS_CBC}

_DEFAULT_INVOICE_TYPE_CODE = "380"
_DEFAULT_UNIT_CODE = "C62"  # UN/ECE "one (piece)" — UBL's default unit.


def _cbc(parent: etree._Element, name: str, text: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{_NS_CBC}}}{name}")
    el.text = text
    return el


def _cac(parent: etree._Element, name: str) -> etree._Element:
    return etree.SubElement(parent, f"{{{_NS_CAC}}}{name}")


def _amount_text(d: Decimal) -> str:
    """Quantize a monetary amount to 2dp and stringify — never float."""
    return str(d.quantize(Decimal("0.01")))


def _quantity_text(d: Decimal) -> str:
    """Quantize a quantity to 4dp (the InvoiceLineItem column scale)."""
    return str(d.quantize(Decimal("0.0001")))


def _amount_el(
    parent: etree._Element, name: str, value: Decimal | None, currency: str | None
) -> None:
    """Emit a monetary cbc element with the currencyID attribute. Skipped when
    ``value`` is None so round-trip omits absent optional fields."""
    if value is None:
        return
    el = _cbc(parent, name, _amount_text(value))
    if currency:
        el.set("currencyID", currency)


def _build_party(parent_aggregate: etree._Element, party: EInvoiceParty) -> None:
    """Serialize an :class:`EInvoiceParty` as the inverse of ``ubl._parse_party``."""
    party_el = _cac(parent_aggregate, "Party")

    if party.name:
        party_name = _cac(party_el, "PartyName")
        _cbc(party_name, "Name", party.name)

    has_address = any(
        (
            party.address_lines,
            party.city,
            party.postal_code,
            party.country_code,
        )
    )
    if has_address:
        addr = _cac(party_el, "PostalAddress")
        # ubl._parse_party reads StreetName, AdditionalStreetName, BuildingNumber
        # in order into address_lines — emit them back in the same order.
        line_tags = ("StreetName", "AdditionalStreetName", "BuildingNumber")
        for tag, line in zip(line_tags, party.address_lines, strict=False):
            if line:
                _cbc(addr, tag, line)
        if party.city:
            _cbc(addr, "CityName", party.city)
        if party.postal_code:
            _cbc(addr, "PostalZone", party.postal_code)
        if party.country_code:
            country = _cac(addr, "Country")
            _cbc(country, "IdentificationCode", party.country_code)

    if party.tax_id:
        tax_scheme_party = _cac(party_el, "PartyTaxScheme")
        _cbc(tax_scheme_party, "CompanyID", party.tax_id)
        scheme = _cac(tax_scheme_party, "TaxScheme")
        _cbc(scheme, "ID", "VAT")

    # PartyLegalEntity carries RegistrationName (parser's name fallback) +
    # the company registration id. Emit when either is present.
    if party.name or party.registration_id:
        legal = _cac(party_el, "PartyLegalEntity")
        if party.name:
            _cbc(legal, "RegistrationName", party.name)
        if party.registration_id:
            _cbc(legal, "CompanyID", party.registration_id)

    if party.email:
        contact = _cac(party_el, "Contact")
        _cbc(contact, "ElectronicMail", party.email)


def _build_tax_total(root: etree._Element, doc: EInvoiceDocument) -> None:
    """cac:TaxTotal — cbc:TaxAmount + one cac:TaxSubtotal per doc.taxes entry."""
    tax_total = _cac(root, "TaxTotal")
    _amount_el(tax_total, "TaxAmount", doc.tax_total, doc.currency)
    for tax in doc.taxes:
        _build_tax_subtotal(tax_total, tax, doc.currency)


def _build_tax_subtotal(parent: etree._Element, tax: EInvoiceTax, currency: str | None) -> None:
    sub = _cac(parent, "TaxSubtotal")
    _amount_el(sub, "TaxableAmount", tax.taxable_amount, currency)
    _amount_el(sub, "TaxAmount", tax.tax_amount, currency)
    category = _cac(sub, "TaxCategory")
    if tax.category:
        _cbc(category, "ID", tax.category)
    if tax.rate is not None:
        _cbc(category, "Percent", _amount_text(tax.rate))
    scheme = _cac(category, "TaxScheme")
    _cbc(scheme, "ID", "VAT")


def _build_monetary_total(root: etree._Element, doc: EInvoiceDocument) -> None:
    lmt = _cac(root, "LegalMonetaryTotal")
    _amount_el(lmt, "LineExtensionAmount", doc.line_extension_amount, doc.currency)
    _amount_el(lmt, "TaxExclusiveAmount", doc.tax_exclusive_amount, doc.currency)
    _amount_el(lmt, "TaxInclusiveAmount", doc.tax_inclusive_amount, doc.currency)
    _amount_el(lmt, "AllowanceTotalAmount", doc.allowance_total, doc.currency)
    _amount_el(lmt, "ChargeTotalAmount", doc.charge_total, doc.currency)
    _amount_el(lmt, "PayableAmount", doc.payable_amount, doc.currency)


def _build_line(root: etree._Element, line: EInvoiceLine, currency: str | None) -> None:
    """cac:InvoiceLine — the inverse of ``ubl._parse_line``."""
    line_el = _cac(root, "InvoiceLine")
    if line.line_id:
        _cbc(line_el, "ID", line.line_id)
    if line.quantity is not None:
        qty = _cbc(line_el, "InvoicedQuantity", _quantity_text(line.quantity))
        qty.set("unitCode", line.unit_code or _DEFAULT_UNIT_CODE)
    _amount_el(line_el, "LineExtensionAmount", line.line_total, currency)

    if line.tax_amount is not None or line.tax_rate is not None:
        line_tax = _cac(line_el, "TaxTotal")
        _amount_el(line_tax, "TaxAmount", line.tax_amount, currency)
        if line.tax_rate is not None:
            sub = _cac(line_tax, "TaxSubtotal")
            category = _cac(sub, "TaxCategory")
            _cbc(category, "Percent", _amount_text(line.tax_rate))

    if line.description or line.item_code:
        item = _cac(line_el, "Item")
        if line.description:
            _cbc(item, "Name", line.description)
        if line.item_code:
            ident = _cac(item, "SellersItemIdentification")
            _cbc(ident, "ID", line.item_code)

    if line.unit_price is not None:
        price = _cac(line_el, "Price")
        _amount_el(price, "PriceAmount", line.unit_price, currency)


def generate_ubl(doc: EInvoiceDocument) -> bytes:
    """Serialize an :class:`EInvoiceDocument` to UBL 2.1 Invoice XML bytes.

    Returns UTF-8 bytes with an XML declaration (mirrors ``parse_ubl``'s
    ``bytes`` input and feeds ``Response(content=...)`` directly).
    """
    root = etree.Element(f"{{{_NS_INVOICE}}}Invoice", nsmap=_NSMAP)

    # Header — element order matches the parser's expectations / the fixture.
    if doc.invoice_number:
        _cbc(root, "ID", doc.invoice_number)
    if doc.issue_date is not None:
        _cbc(root, "IssueDate", doc.issue_date.isoformat())
    if doc.due_date is not None:
        _cbc(root, "DueDate", doc.due_date.isoformat())
    _cbc(root, "InvoiceTypeCode", doc.invoice_type_code or _DEFAULT_INVOICE_TYPE_CODE)
    if doc.currency:
        _cbc(root, "DocumentCurrencyCode", doc.currency)
    if doc.buyer_reference:
        _cbc(root, "BuyerReference", doc.buyer_reference)
    if doc.order_reference:
        order_ref = _cac(root, "OrderReference")
        _cbc(order_ref, "ID", doc.order_reference)

    supplier = _cac(root, "AccountingSupplierParty")
    _build_party(supplier, doc.seller)

    customer = _cac(root, "AccountingCustomerParty")
    _build_party(customer, doc.buyer)

    if doc.payment_means_code:
        means = _cac(root, "PaymentMeans")
        _cbc(means, "PaymentMeansCode", doc.payment_means_code)
    if doc.payment_terms_note:
        terms = _cac(root, "PaymentTerms")
        _cbc(terms, "Note", doc.payment_terms_note)

    # TaxTotal only when there is a tax total or per-rate subtotal to emit.
    if doc.tax_total is not None or doc.taxes:
        _build_tax_total(root, doc)

    _build_monetary_total(root, doc)

    for line in doc.lines:
        _build_line(root, line, doc.currency)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
