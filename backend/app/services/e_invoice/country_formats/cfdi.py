"""CFDI 4.0 (Mexico) outbound e-invoice generator + validator.

Renders the shared :class:`~app.services.e_invoice.model.EInvoiceDocument` into
the Mexican *Comprobante Fiscal Digital por Internet* (CFDI) v4.0 XML
(``cfdi:Comprobante``), the format Mexico's SAT requires for the cleared invoice.

This is a **pure, local-first** unit — no DB, no network, no clock. It emits a
faithful CORE subset derivable from the normalized model and uses SAT catalog
defaults where the model carries no equivalent field (these defaults are noted
inline). It deliberately does **not** invent data the model can't supply.

Deferred clearance step (OUT OF SCOPE for this slice, tracked in
``docs/roadmap.md`` → Automated E-Invoicing): the ``Sello`` / ``Certificado`` /
``NoCertificado`` (the CSD digital seal) and the ``tfd:TimbreFiscalDigital``
complement — which carries the SAT **UUID / folio fiscal** — are produced by a
PAC (Proveedor Autorizado de Certificación) when the invoice is *stamped*
(``timbrado``). That is a networked government-authorized integration; it slots
in behind the same registry as a future adapter. They are **omitted** here. What
ships is the pre-stamping document: generation + national validation, with no
cloud account.

PII invariant (identical to the UBL generator): the generated XML legitimately
carries the emisor/receptor RFC, names, and addresses — that is the document's
purpose — but every :class:`~app.services.e_invoice.validate.FieldError` names
the *field path* + a generic code only, never a value, so nothing PII enters a
log line or an HTTP error body. ``lxml.etree`` escapes text nodes, so a name
containing ``<`` / ``&`` cannot inject markup.
"""

from __future__ import annotations

from decimal import Decimal

from lxml import etree

from app.services.e_invoice.country_formats.base import CountryEInvoiceFormat
from app.services.e_invoice.country_formats.dispatcher import register_country_format
from app.services.e_invoice.model import EInvoiceDocument
from app.services.e_invoice.tax_rules import validate_tax_id
from app.services.e_invoice.validate import FieldError, validate_document

# CFDI 4.0 namespace + prefix.
_NS_CFDI = "http://www.sat.gob.mx/cfd/4"
_NSMAP = {"cfdi": _NS_CFDI}
_VERSION = "4.0"

# SAT catalog defaults — the normalized model carries no equivalent, so the
# most common values are used. A live integration would source these from the
# emisor profile / per-line product mapping.
_DEFAULT_REGIMEN_FISCAL = "601"  # General de Ley Personas Morales
_DEFAULT_USO_CFDI = "G03"  # Gastos en general
_DEFAULT_CLAVE_PROD_SERV = "01010101"  # "No existe en el catálogo" placeholder
_DEFAULT_CLAVE_UNIDAD = "H87"  # Pieza
_TIPO_COMPROBANTE_INGRESO = "I"  # Ingreso (revenue invoice)
_EXPORTACION_NO = "01"  # No aplica
_OBJETO_IMP_SI = "02"  # Sí objeto de impuesto


def _cfdi(parent: etree._Element, name: str) -> etree._Element:
    return etree.SubElement(parent, f"{{{_NS_CFDI}}}{name}")


def _amount_text(d: Decimal) -> str:
    """Quantize a monetary amount to 2dp and stringify — never float."""
    return str(d.quantize(Decimal("0.01")))


@register_country_format("cfdi")
class CFDIFormat(CountryEInvoiceFormat):
    """CFDI 4.0 (Mexico) — ``cfdi:Comprobante`` generator + validator."""

    format_code = "cfdi"
    country = "MX"
    display_name = "CFDI 4.0 (Mexico)"

    def generate(self, doc: EInvoiceDocument) -> bytes:
        root = etree.Element(f"{{{_NS_CFDI}}}Comprobante", nsmap=_NSMAP)
        root.set("Version", _VERSION)
        if doc.issue_date is not None:
            # CFDI Fecha is an ISO-8601 dateTime; the model only has a date.
            root.set("Fecha", f"{doc.issue_date.isoformat()}T00:00:00")
        if doc.currency:
            root.set("Moneda", doc.currency)
        if doc.tax_exclusive_amount is not None:
            root.set("SubTotal", _amount_text(doc.tax_exclusive_amount))
        if doc.payable_amount is not None:
            root.set("Total", _amount_text(doc.payable_amount))
        root.set("TipoDeComprobante", _TIPO_COMPROBANTE_INGRESO)
        root.set("Exportacion", _EXPORTACION_NO)

        # --- Emisor (seller) ---
        emisor = _cfdi(root, "Emisor")
        if doc.seller.tax_id:
            emisor.set("Rfc", doc.seller.tax_id)
        if doc.seller.name:
            emisor.set("Nombre", doc.seller.name)
        emisor.set("RegimenFiscal", _DEFAULT_REGIMEN_FISCAL)

        # --- Receptor (buyer) ---
        receptor = _cfdi(root, "Receptor")
        if doc.buyer.tax_id:
            receptor.set("Rfc", doc.buyer.tax_id)
        if doc.buyer.name:
            receptor.set("Nombre", doc.buyer.name)
        if doc.buyer.postal_code:
            receptor.set("DomicilioFiscalReceptor", doc.buyer.postal_code)
        receptor.set("RegimenFiscalReceptor", _DEFAULT_REGIMEN_FISCAL)
        receptor.set("UsoCFDI", _DEFAULT_USO_CFDI)

        # --- Conceptos (line items) ---
        conceptos = _cfdi(root, "Conceptos")
        for line in doc.lines:
            concepto = _cfdi(conceptos, "Concepto")
            concepto.set("ClaveProdServ", line.item_code or _DEFAULT_CLAVE_PROD_SERV)
            if line.quantity is not None:
                concepto.set("Cantidad", _amount_text(line.quantity))
            concepto.set("ClaveUnidad", line.unit_code or _DEFAULT_CLAVE_UNIDAD)
            if line.description:
                concepto.set("Descripcion", line.description)
            if line.unit_price is not None:
                concepto.set("ValorUnitario", _amount_text(line.unit_price))
            if line.line_total is not None:
                concepto.set("Importe", _amount_text(line.line_total))
            concepto.set("ObjetoImp", _OBJETO_IMP_SI)

        # --- Impuestos (document-level tax summary) ---
        impuestos = _cfdi(root, "Impuestos")
        if doc.tax_total is not None:
            impuestos.set("TotalImpuestosTrasladados", _amount_text(doc.tax_total))

        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)

    # --------------------------------------------------------------- validate
    def validate(self, doc: EInvoiceDocument) -> list[FieldError]:
        errors = validate_document(doc, check_tax=False)

        # CFDI requires both the emisor (seller) and receptor (buyer) RFC.
        for path, party in (("seller", doc.seller), ("buyer", doc.buyer)):
            if not party.tax_id:
                errors.append(
                    FieldError(
                        f"{path}.tax_id",
                        "missing",
                        "CFDI requires an RFC for this party",
                    )
                )
            elif validate_tax_id("MX", party.tax_id):
                errors.append(
                    FieldError(
                        f"{path}.tax_id",
                        "malformed",
                        "RFC format is invalid for Mexico",
                    )
                )

        if doc.payable_amount is None:
            errors.append(
                FieldError("payable_amount", "missing", "CFDI requires the document Total")
            )
        if doc.tax_exclusive_amount is None:
            errors.append(
                FieldError(
                    "tax_exclusive_amount",
                    "missing",
                    "CFDI requires the document SubTotal",
                )
            )

        return errors
