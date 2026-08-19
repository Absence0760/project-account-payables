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

# SAT catalog `c_ObjetoImp` (CFDI 4.0, Anexo 20). Which value a Concepto carries
# is a CLAIM about that line, and the schema's validation rules key off it:
#   "01" - no objeto de impuesto: the Concepto must NOT carry a `cfdi:Impuestos`
#          node. Saying this about a taxed line is a false claim.
#   "02" - si objeto de impuesto: the Concepto MUST carry
#          `cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado`. Claiming "02" without
#          it is exactly the document a PAC refuses to stamp.
#   "03" - si objeto del impuesto y no obligado al desglose: subject to tax, and
#          the Concepto must NOT carry the breakdown. This is the honest answer
#          when the line is taxed but the normalized model cannot establish the
#          rate the breakdown needs.
_OBJETO_IMP_SI = "02"
_OBJETO_IMP_NO = "01"
_OBJETO_IMP_SI_SIN_DESGLOSE = "03"

# `c_Impuesto` 002 = IVA. The normalized model carries UNCL5305 categories
# ("S"/"Z"/"E"), not SAT tax codes, and IVA is the transferred tax on a Mexican
# commercial invoice - a documented default in the same spirit as
# `_DEFAULT_REGIMEN_FISCAL` / `_DEFAULT_USO_CFDI` above.
_IMPUESTO_IVA = "002"
# `c_TipoFactor` "Tasa" (a percentage rate) - never "Exento". A zero rate in the
# model means "taxed at 0%" (tasa cero), for which SAT requires
# TasaOCuota="0.000000" + Importe="0.00"; "Exento" is a DIFFERENT claim (exempt,
# with both attributes absent) and nothing in the normalized model distinguishes
# the two, so we never assert it.
_TIPO_FACTOR_TASA = "Tasa"

_RATE_QUANT = Decimal("0.000001")  # TasaOCuota is a 6-dp FRACTION, not a percent
_HUNDRED = Decimal("100")


def _cfdi(parent: etree._Element, name: str) -> etree._Element:
    return etree.SubElement(parent, f"{{{_NS_CFDI}}}{name}")


def _amount_text(d: Decimal) -> str:
    """Quantize a monetary amount to 2dp and stringify - never float."""
    return str(d.quantize(Decimal("0.01")))


def _rate_fraction_text(rate_percent: Decimal) -> str:
    """Render a percent rate as CFDI's `TasaOCuota` - a 6-dp FRACTION.

    The normalized model stores a percentage (`Decimal("16.00")`); SAT wants
    `0.160000`. Exact Decimal arithmetic throughout - never float.
    """
    return str((rate_percent / _HUNDRED).quantize(_RATE_QUANT))


def _document_tax_rate(doc: EInvoiceDocument) -> Decimal | None:
    """The document's single distinct tax rate, or None.

    Used as the per-line fallback: `mapper.invoice_to_e_invoice` fills
    `EInvoiceLine.tax_amount` but NOT `tax_rate`, so without this fallback the
    common single-rate invoice could never state a line's `TasaOCuota`. With
    more than one distinct rate on the document no line can borrow one - that
    would be a guess.
    """
    rates = {t.rate for t in doc.taxes if t.rate is not None}
    return rates.pop() if len(rates) == 1 else None


def _traslado_attrs(
    *, base: Decimal | None, rate_percent: Decimal | None, importe: Decimal | None
) -> dict[str, str] | None:
    """Build one `cfdi:Traslado`'s attributes, or None when not establishable.

    SAT requires a positive `Base` and, for `TipoFactor="Tasa"`, both
    `TasaOCuota` and `Importe`. `Importe` is *defined* as Base x TasaOCuota, so
    deriving it when the model carries no explicit figure is arithmetic, not a
    guess; a figure the model DOES carry always wins.
    """
    if base is None or rate_percent is None or base <= 0:
        return None
    amount = importe if importe is not None else base * rate_percent / _HUNDRED
    return {
        "Base": _amount_text(base),
        "Impuesto": _IMPUESTO_IVA,
        "TipoFactor": _TIPO_FACTOR_TASA,
        "TasaOCuota": _rate_fraction_text(rate_percent),
        "Importe": _amount_text(amount),
    }


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
        doc_rate = _document_tax_rate(doc)
        conceptos = _cfdi(root, "Conceptos")
        for line in doc.lines:
            concepto = _cfdi(conceptos, "Concepto")
            # `ClaveProdServ` is a key from SAT's `c_ClaveProdServ` catalog, NOT
            # a free identifier — and `EInvoiceLine.item_code` is the SELLER's
            # own part number (`cac:SellersItemIdentification` in UBL,
            # `ram:SellerAssignedID` in CII). Putting the SKU there produced a
            # document the PAC refuses to stamp. The seller's part number has
            # its own attribute, `NoIdentificacion`, so nothing is lost; the
            # catalog key stays the documented "not in the catalog" placeholder
            # until a real per-line SAT mapping exists (deferred with the rest
            # of the clearance step — see the module docstring).
            concepto.set("ClaveProdServ", _DEFAULT_CLAVE_PROD_SERV)
            if line.item_code:
                concepto.set("NoIdentificacion", line.item_code)
            if line.quantity is not None:
                concepto.set("Cantidad", _amount_text(line.quantity))
            concepto.set("ClaveUnidad", line.unit_code or _DEFAULT_CLAVE_UNIDAD)
            if line.description:
                concepto.set("Descripcion", line.description)
            if line.unit_price is not None:
                concepto.set("ValorUnitario", _amount_text(line.unit_price))
            if line.line_total is not None:
                concepto.set("Importe", _amount_text(line.line_total))

            # `ObjetoImp` and the line's own tax breakdown are one decision, not
            # two. Every Concepto used to be stamped "02" (subject to tax) while
            # the `cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado` that "02"
            # REQUIRES was never emitted - a document a PAC refuses to stamp,
            # and a claim the file itself contradicts.
            traslado = _traslado_attrs(
                base=line.line_total,
                rate_percent=line.tax_rate if line.tax_rate is not None else doc_rate,
                importe=line.tax_amount,
            )
            if traslado is not None:
                concepto.set("ObjetoImp", _OBJETO_IMP_SI)
                line_impuestos = _cfdi(concepto, "Impuestos")
                traslados = _cfdi(line_impuestos, "Traslados")
                _cfdi(traslados, "Traslado").attrib.update(traslado)
            elif line.tax_amount:
                # Taxed, but the rate the breakdown needs is not establishable
                # (multi-rate document, or a tax amount with no rate anywhere).
                # "03" says exactly that; "01" would claim the line is not
                # subject to tax at all, which is false.
                concepto.set("ObjetoImp", _OBJETO_IMP_SI_SIN_DESGLOSE)
            else:
                concepto.set("ObjetoImp", _OBJETO_IMP_NO)

        # --- Impuestos (document-level tax summary) ---
        # Emitted only when there is something to report. `TotalImpuestosTrasladados`
        # carries the total; SAT wants the `cfdi:Traslados` breakdown beside it,
        # built from the same establishable inputs as the line-level one.
        doc_traslado = _traslado_attrs(
            base=doc.tax_exclusive_amount,
            rate_percent=doc_rate,
            importe=doc.tax_total,
        )
        if doc.tax_total is not None or doc_traslado is not None:
            impuestos = _cfdi(root, "Impuestos")
            if doc.tax_total is not None:
                impuestos.set("TotalImpuestosTrasladados", _amount_text(doc.tax_total))
            if doc_traslado is not None:
                doc_traslados = _cfdi(impuestos, "Traslados")
                _cfdi(doc_traslados, "Traslado").attrib.update(doc_traslado)

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
