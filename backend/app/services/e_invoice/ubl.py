"""Parse a UBL 2.1 Invoice into the normalized :class:`EInvoiceDocument`.

UBL (used by PEPPOL BIS Billing 3.0) puts header fields in the ``cbc:``
(CommonBasicComponents) namespace and aggregates in ``cac:``
(CommonAggregateComponents). We match by local name (namespace-prefix-agnostic)
via the shared ``_xml`` helpers, so prefix variations don't break parsing.

All amounts go through ``to_decimal`` — money stays exact, never float.
"""

from __future__ import annotations

from app.services.e_invoice._xml import (
    find_all_local,
    find_path,
    find_text,
    local_name,
    parse_secure,
    to_date,
    to_decimal,
)
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)


def _parse_party(party_el) -> EInvoiceParty:
    party = EInvoiceParty()
    if party_el is None:
        return party

    # PartyName/Name, fall back to PartyLegalEntity/RegistrationName.
    party.name = find_text(party_el, "PartyName", "Name") or find_text(
        party_el, "PartyLegalEntity", "RegistrationName"
    )
    # VAT id: PartyTaxScheme/CompanyID.
    party.tax_id = find_text(party_el, "PartyTaxScheme", "CompanyID")
    party.registration_id = find_text(party_el, "PartyLegalEntity", "CompanyID")
    party.email = find_text(party_el, "Contact", "ElectronicMail")

    addr = find_path(party_el, "PostalAddress")
    if addr is not None:
        lines: list[str] = []
        for tag in ("StreetName", "AdditionalStreetName", "BuildingNumber"):
            val = find_text(addr, tag)
            if val:
                lines.append(val)
        party.address_lines = lines
        party.city = find_text(addr, "CityName")
        party.postal_code = find_text(addr, "PostalZone")
        party.country_code = find_text(addr, "Country", "IdentificationCode")
    return party


def _parse_tax_subtotals(tax_total_el) -> list[EInvoiceTax]:
    taxes: list[EInvoiceTax] = []
    if tax_total_el is None:
        return taxes
    for sub in tax_total_el:
        if not isinstance(sub.tag, str) or local_name(sub) != "TaxSubtotal":
            continue
        taxes.append(
            EInvoiceTax(
                category=find_text(sub, "TaxCategory", "ID"),
                rate=to_decimal(find_text(sub, "TaxCategory", "Percent")),
                taxable_amount=to_decimal(find_text(sub, "TaxableAmount")),
                tax_amount=to_decimal(find_text(sub, "TaxAmount")),
            )
        )
    return taxes


def _parse_line(line_el) -> EInvoiceLine:
    line = EInvoiceLine()
    line.line_id = find_text(line_el, "ID")
    line.description = find_text(line_el, "Item", "Name") or find_text(
        line_el, "Item", "Description"
    )
    line.item_code = find_text(line_el, "Item", "SellersItemIdentification", "ID")
    line.line_total = to_decimal(find_text(line_el, "LineExtensionAmount"))

    qty_el = find_path(line_el, "InvoicedQuantity")
    if qty_el is not None:
        line.quantity = to_decimal(qty_el.text)
        line.unit_code = qty_el.get("unitCode")

    line.unit_price = to_decimal(find_text(line_el, "Price", "PriceAmount"))

    line_tax = find_path(line_el, "TaxTotal")
    if line_tax is not None:
        line.tax_amount = to_decimal(find_text(line_tax, "TaxAmount"))
        line.tax_rate = to_decimal(find_text(line_tax, "TaxSubtotal", "TaxCategory", "Percent"))
    return line


def parse_ubl(xml_bytes: bytes) -> EInvoiceDocument:
    """Map a UBL 2.1 Invoice document into the normalized model."""
    root = parse_secure(xml_bytes)
    doc = EInvoiceDocument(source_format=EInvoiceFormat.UBL)
    doc.raw_xml_root_tag = local_name(root)

    doc.invoice_number = find_text(root, "ID")
    doc.issue_date = to_date(find_text(root, "IssueDate"))
    doc.due_date = to_date(find_text(root, "DueDate"))
    doc.currency = find_text(root, "DocumentCurrencyCode")
    doc.invoice_type_code = find_text(root, "InvoiceTypeCode")
    doc.buyer_reference = find_text(root, "BuyerReference")
    doc.order_reference = find_text(root, "OrderReference", "ID")

    doc.seller = _parse_party(find_path(root, "AccountingSupplierParty", "Party"))
    doc.buyer = _parse_party(find_path(root, "AccountingCustomerParty", "Party"))

    doc.payment_means_code = find_text(root, "PaymentMeans", "PaymentMeansCode")
    doc.payment_terms_note = find_text(root, "PaymentTerms", "Note")

    tax_total_el = find_path(root, "TaxTotal")
    if tax_total_el is not None:
        doc.tax_total = to_decimal(find_text(tax_total_el, "TaxAmount"))
        doc.taxes = _parse_tax_subtotals(tax_total_el)

    lmt = find_path(root, "LegalMonetaryTotal")
    if lmt is not None:
        doc.line_extension_amount = to_decimal(find_text(lmt, "LineExtensionAmount"))
        doc.tax_exclusive_amount = to_decimal(find_text(lmt, "TaxExclusiveAmount"))
        doc.tax_inclusive_amount = to_decimal(find_text(lmt, "TaxInclusiveAmount"))
        doc.allowance_total = to_decimal(find_text(lmt, "AllowanceTotalAmount"))
        doc.charge_total = to_decimal(find_text(lmt, "ChargeTotalAmount"))
        doc.payable_amount = to_decimal(find_text(lmt, "PayableAmount"))

    doc.lines = [_parse_line(el) for el in find_all_local(root, "InvoiceLine")]
    return doc
