"""NF-e (Brazil) outbound e-invoice generator + validator.

Renders the shared :class:`~app.services.e_invoice.model.EInvoiceDocument` into
the Brazilian *Nota Fiscal Eletrônica* (NF-e) XML — the ``NFe`` document with an
``infNFe`` group at layout version ``4.00``, the format the state *SEFAZ*
authorities authorize for the cleared fiscal document.

This is a **pure, local-first** unit — no DB, no network, no clock. It emits a
faithful CORE subset of the NF-e schema derivable from the normalized model; it
deliberately does **not** invent data the model can't supply.

Deferred clearance step (OUT OF SCOPE for this slice, tracked in
``docs/roadmap.md`` → Automated E-Invoicing): authorization at the state SEFAZ
is a networked government integration. On authorization SEFAZ assigns:

* the 44-digit **chave de acesso** (the access key that uniquely identifies the
  NF-e nationally) — this slice emits a deterministic, clearly-non-authoritative
  placeholder ``Id`` derived from the invoice number so the structural envelope
  is well-formed;
* the **protocolo de autorização** (authorization protocol number); and
* the **digital signature** (the XML-DSig ``Signature`` block over ``infNFe``).

All three slot in behind the same registry as a future adapter — exactly as
PEPPOL's ``as4_gateway`` did. **NFS-e** (municipal *services* invoices) is a
separate, municipality-specific schema (not the state NF-e schema emitted here)
and is future scope.

PII invariant (identical to the UBL generator): the generated XML legitimately
carries the seller/buyer CNPJ, names, and addresses — that is the document's
purpose — but every :class:`~app.services.e_invoice.validate.FieldError` names
the *field path* + a generic code only, never a value, so nothing PII enters a
log line or an HTTP error body. ``lxml.etree`` escapes text nodes, so a name
containing ``<`` / ``&`` cannot inject markup.
"""

from __future__ import annotations

import re
from decimal import Decimal

from lxml import etree

from app.services.e_invoice.country_formats.base import CountryEInvoiceFormat
from app.services.e_invoice.country_formats.dispatcher import register_country_format
from app.services.e_invoice.model import EInvoiceDocument, EInvoiceParty
from app.services.e_invoice.tax_rules import validate_tax_id
from app.services.e_invoice.validate import FieldError, validate_document

# NF-e v4.00 namespace.
_NS_NFE = "http://www.portalfiscal.inf.br/nfe"
_VERSAO = "4.00"

# Default IBGE state code for cUF when the model can't supply one (35 = São Paulo).
_DEFAULT_CUF = "35"
# mod 55 = NF-e (the goods model emitted here); 65 = NFC-e (consumer) is not used.
_MODELO = "55"
_SERIE = "1"
# tpNF 1 = saída (outbound / sale).
_TIPO_SAIDA = "1"


def _el(parent: etree._Element, name: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{_NS_NFE}}}{name}")
    if text is not None:
        el.text = text
    return el


def _amount_text(d: Decimal) -> str:
    """Quantize a monetary amount to 2dp and stringify — never float."""
    return str(d.quantize(Decimal("0.01")))


def _qty_text(d: Decimal) -> str:
    """Quantize a quantity to 4dp (NF-e qCom carries 4 decimals) — never float."""
    return str(d.quantize(Decimal("0.0001")))


def _placeholder_access_key(invoice_number: str | None) -> str:
    """Deterministic, non-authoritative access-key-shaped id from the invoice number.

    The real 44-digit *chave de acesso* is assigned by SEFAZ at authorization;
    this only gives the structural ``Id`` attribute a stable, clearly-fake value
    (``NFe`` + 44 digits) for the pre-clearance document.
    """
    digits = re.sub(r"\D", "", invoice_number or "")
    # Pad/truncate to the 44-digit chave de acesso shape; prefix the literal
    # "NFe" per the schema's Id attribute convention.
    return "NFe" + (digits or "0").rjust(44, "0")[:44]


@register_country_format("nfe")
class NFeFormat(CountryEInvoiceFormat):
    """NF-e (Brazil) — ``NFe`` / ``infNFe`` v4.00 generator + validator."""

    format_code = "nfe"
    country = "BR"
    display_name = "NF-e (Brazil)"

    # ------------------------------------------------------------------ build
    def _build_emit(self, parent: etree._Element, party: EInvoiceParty) -> None:
        """``emit`` — the seller (emitente): CNPJ + name + address."""
        emit = _el(parent, "emit")
        if party.tax_id:
            _el(emit, "CNPJ", party.tax_id)
        if party.name:
            _el(emit, "xNome", party.name)
        has_address = any((party.address_lines, party.city, party.postal_code, party.country_code))
        if has_address:
            ender = _el(emit, "enderEmit")
            if party.address_lines:
                _el(ender, "xLgr", party.address_lines[0])
            if party.city:
                _el(ender, "xMun", party.city)
            if party.postal_code:
                _el(ender, "CEP", party.postal_code)

    def _build_dest(self, parent: etree._Element, party: EInvoiceParty) -> None:
        """``dest`` — the buyer (destinatário): CNPJ + name."""
        dest = _el(parent, "dest")
        if party.tax_id:
            _el(dest, "CNPJ", party.tax_id)
        if party.name:
            _el(dest, "xNome", party.name)

    def generate(self, doc: EInvoiceDocument) -> bytes:
        root = etree.Element(f"{{{_NS_NFE}}}NFe", nsmap={None: _NS_NFE})

        inf = _el(root, "infNFe")
        inf.set("versao", _VERSAO)
        inf.set("Id", _placeholder_access_key(doc.invoice_number))

        # --- ide: identificação da NF-e ---
        ide = _el(inf, "ide")
        _el(ide, "cUF", _DEFAULT_CUF)
        _el(ide, "natOp", doc.order_reference or "Venda")
        _el(ide, "mod", _MODELO)
        _el(ide, "serie", _SERIE)
        if doc.invoice_number:
            _el(ide, "nNF", doc.invoice_number)
        if doc.issue_date is not None:
            _el(ide, "dhEmi", f"{doc.issue_date.isoformat()}T00:00:00")
        _el(ide, "tpNF", _TIPO_SAIDA)

        # --- emit (seller) / dest (buyer) ---
        self._build_emit(inf, doc.seller)
        self._build_dest(inf, doc.buyer)

        # --- det: one per line ---
        for i, line in enumerate(doc.lines, start=1):
            det = _el(inf, "det")
            det.set("nItem", str(i))
            prod = _el(det, "prod")
            _el(prod, "cProd", line.item_code or str(i))
            if line.description:
                _el(prod, "xProd", line.description)
            if line.quantity is not None:
                _el(prod, "qCom", _qty_text(line.quantity))
            if line.unit_price is not None:
                _el(prod, "vUnCom", _amount_text(line.unit_price))
            if line.line_total is not None:
                _el(prod, "vProd", _amount_text(line.line_total))

        # --- total / ICMSTot ---
        total = _el(inf, "total")
        icms_tot = _el(total, "ICMSTot")
        if doc.tax_exclusive_amount is not None:
            _el(icms_tot, "vProd", _amount_text(doc.tax_exclusive_amount))
        if doc.payable_amount is not None:
            _el(icms_tot, "vNF", _amount_text(doc.payable_amount))

        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)

    # --------------------------------------------------------------- validate
    def validate(self, doc: EInvoiceDocument) -> list[FieldError]:
        errors = validate_document(doc, check_tax=False)

        # NF-e mandates the emitente (seller) carry a CNPJ.
        if not doc.seller.tax_id:
            errors.append(
                FieldError(
                    "seller.tax_id",
                    "missing",
                    "NF-e requires the emitente (seller) CNPJ",
                )
            )
        elif validate_tax_id("BR", doc.seller.tax_id):
            errors.append(
                FieldError(
                    "seller.tax_id",
                    "malformed",
                    "CNPJ format is invalid for Brazil",
                )
            )

        if doc.payable_amount is None:
            errors.append(
                FieldError(
                    "payable_amount",
                    "missing",
                    "NF-e requires the document total (ICMSTot/vNF)",
                )
            )

        return errors
