"""Parse a UN/CEFACT CII (Cross Industry Invoice, D16B) into the model.

CII is the XML dialect Factur-X / ZUGFeRD embed inside a PDF/A-3 (it is NOT
UBL). Header data lives under ``rsm:ExchangedDocument``; the bulk lives under
``rsm:SupplyChainTradeTransaction`` split into three ``ram:`` aggregates:
- ApplicableHeaderTradeAgreement   → parties + order reference
- ApplicableHeaderTradeDelivery    → (delivery; not mapped this slice)
- ApplicableHeaderTradeSettlement  → currency, payment, tax, monetary totals

We match by local name (ns-prefix-agnostic) via the shared ``_xml`` helpers.
All amounts via ``to_decimal`` — money stays exact, never float.
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

    party.name = find_text(party_el, "Name")
    # CII carries multiple SpecifiedTaxRegistration/ID (VA = VAT, FC = tax).
    for reg in party_el:
        if not isinstance(reg.tag, str) or local_name(reg) != "SpecifiedTaxRegistration":
            continue
        tid = find_text(reg, "ID")
        if tid and party.tax_id is None:
            party.tax_id = tid
    party.registration_id = find_text(party_el, "SpecifiedLegalOrganization", "ID")
    party.email = find_text(party_el, "URIUniversalCommunication", "URIID")

    addr = find_path(party_el, "PostalTradeAddress")
    if addr is not None:
        lines: list[str] = []
        for tag in ("LineOne", "LineTwo", "LineThree"):
            val = find_text(addr, tag)
            if val:
                lines.append(val)
        party.address_lines = lines
        party.city = find_text(addr, "CityName")
        party.postal_code = find_text(addr, "PostcodeCode")
        party.country_code = find_text(addr, "CountryID")
    return party


def _parse_taxes(settlement_el) -> list[EInvoiceTax]:
    taxes: list[EInvoiceTax] = []
    if settlement_el is None:
        return taxes
    for tax in settlement_el:
        if not isinstance(tax.tag, str) or local_name(tax) != "ApplicableTradeTax":
            continue
        taxes.append(
            EInvoiceTax(
                category=find_text(tax, "CategoryCode"),
                rate=to_decimal(find_text(tax, "RateApplicablePercent")),
                taxable_amount=to_decimal(find_text(tax, "BasisAmount")),
                tax_amount=to_decimal(find_text(tax, "CalculatedAmount")),
            )
        )
    return taxes


def _parse_line(line_el) -> EInvoiceLine:
    line = EInvoiceLine()
    line.line_id = find_text(line_el, "AssociatedDocumentLineDocument", "LineID")

    product = find_path(line_el, "SpecifiedTradeProduct")
    if product is not None:
        line.description = find_text(product, "Name")
        line.item_code = find_text(product, "SellerAssignedID") or find_text(product, "GlobalID")

    agreement = find_path(line_el, "SpecifiedLineTradeAgreement")
    if agreement is not None:
        line.unit_price = to_decimal(
            find_text(agreement, "NetPriceProductTradePrice", "ChargeAmount")
        ) or to_decimal(find_text(agreement, "GrossPriceProductTradePrice", "ChargeAmount"))

    delivery = find_path(line_el, "SpecifiedLineTradeDelivery")
    if delivery is not None:
        qty_el = find_path(delivery, "BilledQuantity")
        if qty_el is not None:
            line.quantity = to_decimal(qty_el.text)
            line.unit_code = qty_el.get("unitCode")

    settlement = find_path(line_el, "SpecifiedLineTradeSettlement")
    if settlement is not None:
        line.tax_rate = to_decimal(
            find_text(settlement, "ApplicableTradeTax", "RateApplicablePercent")
        )
        # The line's own tax figure. UBL's parser has always read it
        # (`cac:TaxTotal/cbc:TaxAmount`); CII's did not, so every Factur-X /
        # ZUGFeRD line arrived with `tax_amount=None` and the extraction
        # adapter — which maps `line.tax_amount` onto `ExtractedLineItem.tax` —
        # silently dropped per-line tax the document actually carried.
        line.tax_amount = to_decimal(
            find_text(settlement, "ApplicableTradeTax", "CalculatedAmount")
        )
        line.line_total = to_decimal(
            find_text(
                settlement,
                "SpecifiedTradeSettlementLineMonetarySummation",
                "LineTotalAmount",
            )
        )
    return line


def parse_cii(xml_bytes: bytes) -> EInvoiceDocument:
    """Map a UN/CEFACT CII CrossIndustryInvoice into the normalized model."""
    root = parse_secure(xml_bytes)
    doc = EInvoiceDocument(source_format=EInvoiceFormat.CII)
    doc.raw_xml_root_tag = local_name(root)

    exchanged = find_path(root, "ExchangedDocument")
    if exchanged is not None:
        doc.invoice_number = find_text(exchanged, "ID")
        doc.invoice_type_code = find_text(exchanged, "TypeCode")
        doc.issue_date = to_date(find_text(exchanged, "IssueDateTime", "DateTimeString"))

    txn = find_path(root, "SupplyChainTradeTransaction")
    if txn is None:
        return doc

    agreement = find_path(txn, "ApplicableHeaderTradeAgreement")
    if agreement is not None:
        doc.buyer_reference = find_text(agreement, "BuyerReference")
        doc.seller = _parse_party(find_path(agreement, "SellerTradeParty"))
        doc.buyer = _parse_party(find_path(agreement, "BuyerTradeParty"))
        doc.order_reference = find_text(
            agreement, "BuyerOrderReferencedDocument", "IssuerAssignedID"
        )

    settlement = find_path(txn, "ApplicableHeaderTradeSettlement")
    if settlement is not None:
        doc.currency = find_text(settlement, "InvoiceCurrencyCode")
        doc.payment_terms_note = find_text(settlement, "SpecifiedTradePaymentTerms", "Description")
        doc.due_date = to_date(
            find_text(
                settlement,
                "SpecifiedTradePaymentTerms",
                "DueDateDateTime",
                "DateTimeString",
            )
        )
        doc.payment_means_code = find_text(
            settlement, "SpecifiedTradeSettlementPaymentMeans", "TypeCode"
        )
        doc.taxes = _parse_taxes(settlement)

        summation = find_path(settlement, "SpecifiedTradeSettlementHeaderMonetarySummation")
        if summation is not None:
            doc.line_extension_amount = to_decimal(find_text(summation, "LineTotalAmount"))
            doc.tax_exclusive_amount = to_decimal(find_text(summation, "TaxBasisTotalAmount"))
            doc.tax_total = to_decimal(find_text(summation, "TaxTotalAmount")) or doc.tax_total
            doc.allowance_total = to_decimal(find_text(summation, "AllowanceTotalAmount"))
            doc.charge_total = to_decimal(find_text(summation, "ChargeTotalAmount"))
            doc.tax_inclusive_amount = to_decimal(find_text(summation, "GrandTotalAmount"))
            doc.payable_amount = to_decimal(find_text(summation, "DuePayableAmount"))

    doc.lines = [_parse_line(el) for el in find_all_local(txn, "IncludedSupplyChainTradeLineItem")]
    return doc
