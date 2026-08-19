"""DIAN (Colombia) outbound e-invoice generator + validator.

Colombia's *factura electrónica de venta* is **UBL 2.1** with DIAN-specific
profiling (the DIAN technical annex, *Factura Electrónica de Venta* v2.1). This
module renders the shared :class:`~app.services.e_invoice.model.EInvoiceDocument`
into that DIAN-profiled UBL ``Invoice`` — same namespaces as the pan-European
UBL generator in :mod:`app.services.e_invoice.generate`, but with the
DIAN-mandated ``CustomizationID`` / ``ProfileID`` and the ``ext:UBLExtensions``
envelope the clearance fields live in.

This is a **pure, local-first** unit — no DB, no network, no clock. It emits the
structural envelope derivable from the normalized model; it deliberately does
**not** invent data the model can't supply.

Deferred clearance step (OUT OF SCOPE for this slice, tracked in
``docs/roadmap.md`` → Automated E-Invoicing): authorization at DIAN is a
networked government integration. The DIAN-specific clearance fields populated
at authorization are emitted here only as an empty ``ext:UBLExtensions``
placeholder:

* the **CUFE** (*código único de factura electrónica* — the document's unique
  hash-derived code);
* the **XAdES digital signature** (an enveloped ``ds:Signature`` over the
  invoice); and
* the ``dian:DianExtensions`` block — ``InvoiceControl`` (the authorized
  numbering range + resolution), ``InvoiceSource``, ``SoftwareProvider``, and
  the ``SoftwareSecurityCode``.

All slot in behind the same registry as a future adapter — exactly as PEPPOL's
``as4_gateway`` did. What ships here is the pre-clearance UBL envelope:
generation + national validation, with no cloud account.

PII invariant (identical to the UBL generator): the generated XML legitimately
carries the seller/buyer NIT and names — that is the document's purpose — but
every :class:`~app.services.e_invoice.validate.FieldError` names the *field
path* + a generic code only, never a value, so nothing PII enters a log line or
an HTTP error body. ``lxml.etree`` escapes text nodes, so a name containing
``<`` / ``&`` cannot inject markup.
"""

from __future__ import annotations

from decimal import Decimal

from lxml import etree

from app.services.e_invoice.country_formats.base import CountryEInvoiceFormat
from app.services.e_invoice.country_formats.dispatcher import register_country_format
from app.services.e_invoice.model import EInvoiceDocument, EInvoiceParty
from app.services.e_invoice.tax_rules import validate_tax_id
from app.services.e_invoice.validate import FieldError, validate_document

# UBL 2.1 namespaces — mirror app.services.e_invoice.generate exactly.
_NS_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
_NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
# CommonExtensionComponents — the envelope the CUFE / signature / DianExtensions
# are injected into at clearance.
_NS_EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"

_NSMAP = {None: _NS_INVOICE, "cac": _NS_CAC, "cbc": _NS_CBC, "ext": _NS_EXT}

_DEFAULT_INVOICE_TYPE_CODE = "01"  # DIAN: 01 = factura de venta nacional.
_DEFAULT_UNIT_CODE = "EA"  # UN/ECE "each".

# DIAN reads `cbc:InvoiceTypeCode` from its OWN document-type list, not
# UNCL1001 — 01 factura de venta nacional · 02 factura de exportación ·
# 03 factura por contingencia facturador · 04 factura de contingencia DIAN.
# `EInvoiceDocument.invoice_type_code` is documented as UNCL1001 and the mapper
# always sets `380`, so passing it through emitted a value outside DIAN's list
# on every export and left the default above unreachable. Translate, exactly as
# `fatturapa._INVOICE_TYPE_TO_TIPO_DOCUMENTO` does. (UNCL1001's credit/debit
# notes have no InvoiceTypeCode target — DIAN models those as separate
# `NotaCredito` / `NotaDebito` documents, which this slice does not emit.)
_DIAN_INVOICE_TYPE_CODES = frozenset({"01", "02", "03", "04"})
_UNCL1001_TO_DIAN_INVOICE_TYPE = {"380": "01"}


def _resolve_invoice_type_code(raw: str | None) -> str:
    """UNCL1001 (or an already-DIAN code) → a DIAN document-type code."""
    code = (raw or "").strip()
    if code in _DIAN_INVOICE_TYPE_CODES:
        return code
    return _UNCL1001_TO_DIAN_INVOICE_TYPE.get(code, _DEFAULT_INVOICE_TYPE_CODE)


# DIAN profiling constants (technical annex v2.1).
_CUSTOMIZATION_ID = "10"  # 10 = instrumento estándar (standard instrument).
_PROFILE_ID = "DIAN 2.1: Factura Electrónica de Venta"
_UBL_VERSION_ID = "UBL 2.1"


def _cbc(parent: etree._Element, name: str, text: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{_NS_CBC}}}{name}")
    el.text = text
    return el


def _cac(parent: etree._Element, name: str) -> etree._Element:
    return etree.SubElement(parent, f"{{{_NS_CAC}}}{name}")


def _amount_text(d: Decimal) -> str:
    """Quantize a monetary amount to 2dp and stringify — never float."""
    return str(d.quantize(Decimal("0.01")))


def _qty_text(d: Decimal) -> str:
    return str(d.quantize(Decimal("0.0001")))


def _amount_el(
    parent: etree._Element, name: str, value: Decimal | None, currency: str | None
) -> None:
    """Emit a monetary cbc element with currencyID. Skipped when value is None."""
    if value is None:
        return
    el = _cbc(parent, name, _amount_text(value))
    if currency:
        el.set("currencyID", currency)


def _build_party(parent_aggregate: etree._Element, party: EInvoiceParty) -> None:
    """DIAN party — PartyName + PartyTaxScheme/CompanyID (the NIT)."""
    party_el = _cac(parent_aggregate, "Party")
    if party.name:
        party_name = _cac(party_el, "PartyName")
        _cbc(party_name, "Name", party.name)
    if party.tax_id:
        tax_scheme_party = _cac(party_el, "PartyTaxScheme")
        _cbc(tax_scheme_party, "CompanyID", party.tax_id)
        scheme = _cac(tax_scheme_party, "TaxScheme")
        _cbc(scheme, "ID", "01")  # DIAN: 01 = IVA.


@register_country_format("dian")
class DIANFormat(CountryEInvoiceFormat):
    """DIAN (Colombia) — DIAN-profiled UBL 2.1 ``Invoice`` generator + validator."""

    format_code = "dian"
    country = "CO"
    display_name = "DIAN (Colombia)"

    # ------------------------------------------------------------------ build
    def generate(self, doc: EInvoiceDocument) -> bytes:
        root = etree.Element(f"{{{_NS_INVOICE}}}Invoice", nsmap=_NSMAP)

        # --- UBLExtensions placeholder: CUFE + ds:Signature + dian:DianExtensions
        # are injected here at clearance. Emitted empty in this pre-clearance slice.
        extensions = etree.SubElement(root, f"{{{_NS_EXT}}}UBLExtensions")
        extension = etree.SubElement(extensions, f"{{{_NS_EXT}}}UBLExtension")
        etree.SubElement(extension, f"{{{_NS_EXT}}}ExtensionContent")

        # --- DIAN profiling header ---
        _cbc(root, "UBLVersionID", _UBL_VERSION_ID)
        _cbc(root, "CustomizationID", _CUSTOMIZATION_ID)
        _cbc(root, "ProfileID", _PROFILE_ID)

        # --- Standard UBL header ---
        if doc.invoice_number:
            _cbc(root, "ID", doc.invoice_number)
        if doc.issue_date is not None:
            _cbc(root, "IssueDate", doc.issue_date.isoformat())
        if doc.due_date is not None:
            _cbc(root, "DueDate", doc.due_date.isoformat())
        _cbc(root, "InvoiceTypeCode", _resolve_invoice_type_code(doc.invoice_type_code))
        if doc.currency:
            _cbc(root, "DocumentCurrencyCode", doc.currency)

        supplier = _cac(root, "AccountingSupplierParty")
        _build_party(supplier, doc.seller)

        customer = _cac(root, "AccountingCustomerParty")
        _build_party(customer, doc.buyer)

        # --- TaxTotal ---
        if doc.tax_total is not None or doc.taxes:
            tax_total = _cac(root, "TaxTotal")
            _amount_el(tax_total, "TaxAmount", doc.tax_total, doc.currency)
            for tax in doc.taxes:
                sub = _cac(tax_total, "TaxSubtotal")
                _amount_el(sub, "TaxableAmount", tax.taxable_amount, doc.currency)
                _amount_el(sub, "TaxAmount", tax.tax_amount, doc.currency)
                category = _cac(sub, "TaxCategory")
                if tax.rate is not None:
                    _cbc(category, "Percent", _amount_text(tax.rate))
                scheme = _cac(category, "TaxScheme")
                _cbc(scheme, "ID", "01")

        # --- LegalMonetaryTotal ---
        lmt = _cac(root, "LegalMonetaryTotal")
        _amount_el(lmt, "LineExtensionAmount", doc.line_extension_amount, doc.currency)
        _amount_el(lmt, "TaxExclusiveAmount", doc.tax_exclusive_amount, doc.currency)
        _amount_el(lmt, "TaxInclusiveAmount", doc.tax_inclusive_amount, doc.currency)
        _amount_el(lmt, "PayableAmount", doc.payable_amount, doc.currency)

        # --- InvoiceLine: one per line ---
        for i, line in enumerate(doc.lines, start=1):
            line_el = _cac(root, "InvoiceLine")
            _cbc(line_el, "ID", line.line_id or str(i))
            if line.quantity is not None:
                qty = _cbc(line_el, "InvoicedQuantity", _qty_text(line.quantity))
                qty.set("unitCode", line.unit_code or _DEFAULT_UNIT_CODE)
            _amount_el(line_el, "LineExtensionAmount", line.line_total, doc.currency)
            if line.description or line.item_code:
                item = _cac(line_el, "Item")
                if line.description:
                    _cbc(item, "Description", line.description)
                if line.item_code:
                    ident = _cac(item, "SellersItemIdentification")
                    _cbc(ident, "ID", line.item_code)
            if line.unit_price is not None:
                price = _cac(line_el, "Price")
                _amount_el(price, "PriceAmount", line.unit_price, doc.currency)

        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)

    # --------------------------------------------------------------- validate
    def validate(self, doc: EInvoiceDocument) -> list[FieldError]:
        errors = validate_document(doc, check_tax=False)

        # DIAN mandates the seller (emisor) carry a NIT.
        if not doc.seller.tax_id:
            errors.append(
                FieldError(
                    "seller.tax_id",
                    "missing",
                    "DIAN requires the emisor (seller) NIT",
                )
            )
        elif validate_tax_id("CO", doc.seller.tax_id):
            errors.append(
                FieldError(
                    "seller.tax_id",
                    "malformed",
                    "NIT format is invalid for Colombia",
                )
            )

        if doc.payable_amount is None:
            errors.append(
                FieldError(
                    "payable_amount",
                    "missing",
                    "DIAN requires the document total (LegalMonetaryTotal/PayableAmount)",
                )
            )

        return errors
