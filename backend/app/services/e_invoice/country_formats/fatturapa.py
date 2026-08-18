"""FatturaPA (Italy) outbound e-invoice generator + validator.

Renders the shared :class:`~app.services.e_invoice.model.EInvoiceDocument` into
the Italian *FatturaPA* XML (``FatturaElettronica``, schema version ``FPR12``),
the format the Sistema di Interscambio (SdI) requires for the cleared invoice.

This is a **pure, local-first** unit — no DB, no network, no clock. It emits a
faithful CORE subset derivable from the normalized model; it deliberately does
**not** invent data the model can't supply.

Deferred clearance step (OUT OF SCOPE for this slice, tracked in
``docs/roadmap.md`` → Automated E-Invoicing): transmission to the SdI and the
mandatory **digital signature** (the ``.p7m`` CAdES-BES envelope the SdI rejects
the file without) are a networked government integration. They slot in behind
the same registry as a future adapter — exactly as PEPPOL's ``as4_gateway`` did.
The government authorization receipt (the SdI ``IdentificativoSdI``) is assigned
on acceptance and is a documented future follow-up. What ships here is the
pre-clearance document: generation + national validation, with no cloud account.

PII invariant (identical to the UBL generator): the generated XML legitimately
carries the seller/buyer Partita IVA, addresses, and emails — that is the
document's purpose — but every :class:`~app.services.e_invoice.validate.FieldError`
names the *field path* + a generic code only, never a value, so nothing PII
enters a log line or an HTTP error body. ``lxml.etree`` escapes text nodes, so a
name containing ``<`` / ``&`` cannot inject markup.
"""

from __future__ import annotations

from decimal import Decimal

from lxml import etree

from app.services.e_invoice.country_formats.base import CountryEInvoiceFormat
from app.services.e_invoice.country_formats.dispatcher import register_country_format
from app.services.e_invoice.model import EInvoiceDocument, EInvoiceParty
from app.services.e_invoice.tax_rules import validate_tax_id
from app.services.e_invoice.validate import FieldError, validate_document

# FatturaPA v1.2 namespace + transmission format code (FPR12 = privati/B2B).
_NS_FATTURAPA = "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"
_VERSIONE = "FPR12"

# A 7-char placeholder destination code is used when no buyer routing code is
# supplied — the SdI accepts "0000000" for a recipient reached via PEC/other.
_DEFAULT_CODICE_DESTINATARIO = "0000000"

# UNCL1001 380 (commercial invoice) → FatturaPA TipoDocumento TD01.
_DEFAULT_TIPO_DOCUMENTO = "TD01"
_INVOICE_TYPE_TO_TIPO_DOCUMENTO = {
    "380": "TD01",  # commercial invoice
    "381": "TD04",  # credit note
    "383": "TD05",  # debit note
}


def _el(parent: etree._Element, name: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{_NS_FATTURAPA}}}{name}")
    if text is not None:
        el.text = text
    return el


def _amount_text(d: Decimal) -> str:
    """Quantize a monetary amount to 2dp and stringify — never float."""
    return str(d.quantize(Decimal("0.01")))


def _rate_text(d: Decimal) -> str:
    """Quantize an IVA rate to 2dp and stringify — never float."""
    return str(d.quantize(Decimal("0.01")))


# VAT prefixes that differ from the ISO 3166-1 alpha-2 code they identify.
# Greece is the only one: its VAT numbers start `EL`, its ISO code is `GR`.
_VAT_PREFIX_ALIASES = {"EL": "GR"}


def _split_id_fiscale(tax_id: str | None, country_code: str | None) -> tuple[str, str]:
    """Split a VAT id into FatturaPA's ``(IdPaese, IdCodice)`` pair.

    ``IdFiscaleIVA`` is a **two-part** identifier: ``IdPaese`` carries the ISO
    3166-1 alpha-2 country and ``IdCodice`` carries the VAT number *without*
    it. Our normalized model stores the full, country-prefixed id (which is
    what `tax_rules.validate_tax_id` checks — the IT pattern is `^IT\\d{11}$`),
    so emitting it verbatim produced ``IdPaese=IT`` beside
    ``IdCodice=IT12345678901`` — the country stated twice, and an id the SdI
    rejects as malformed.

    The prefix is only stripped when it actually identifies the country being
    emitted (directly, or via the EL→GR alias), so a bare Partita IVA or a
    non-prefixed scheme passes through untouched.
    """
    paese = (country_code or FatturaPAFormat.country).strip().upper()
    raw = (tax_id or "").strip()
    if len(raw) > 2 and raw[:2].isalpha():
        prefix = raw[:2].upper()
        if prefix == paese or _VAT_PREFIX_ALIASES.get(prefix) == paese:
            return paese, raw[2:]
    return paese, raw


@register_country_format("fatturapa")
class FatturaPAFormat(CountryEInvoiceFormat):
    """FatturaPA (Italy) — ``FatturaElettronica`` v1.2 generator + validator."""

    format_code = "fatturapa"
    country = "IT"
    display_name = "FatturaPA (Italy)"

    # ------------------------------------------------------------------ build
    def _build_anagrafici(self, parent: etree._Element, party: EInvoiceParty) -> None:
        """``CedentePrestatore`` / ``CessionarioCommittente`` body — id + address."""
        dati = _el(parent, "DatiAnagrafici")
        if party.tax_id:
            paese, codice = _split_id_fiscale(party.tax_id, party.country_code)
            id_iva = _el(dati, "IdFiscaleIVA")
            _el(id_iva, "IdPaese", paese)
            _el(id_iva, "IdCodice", codice)
        anagrafica = _el(dati, "Anagrafica")
        if party.name:
            _el(anagrafica, "Denominazione", party.name)

        sede = _el(parent, "Sede")
        if party.address_lines:
            _el(sede, "Indirizzo", party.address_lines[0])
        if party.postal_code:
            _el(sede, "CAP", party.postal_code)
        if party.city:
            _el(sede, "Comune", party.city)
        if party.country_code:
            _el(sede, "Nazione", party.country_code)

    def generate(self, doc: EInvoiceDocument) -> bytes:
        root = etree.Element(
            f"{{{_NS_FATTURAPA}}}FatturaElettronica",
            nsmap={"p": _NS_FATTURAPA},
        )
        root.set("versione", _VERSIONE)

        # --- Header ---
        header = _el(root, "FatturaElettronicaHeader")

        trasmissione = _el(header, "DatiTrasmissione")
        id_trasmittente = _el(trasmissione, "IdTrasmittente")
        # Same two-part identifier as IdFiscaleIVA — IdCodice excludes the
        # country prefix that IdPaese already states.
        paese_tx, codice_tx = _split_id_fiscale(doc.seller.tax_id, doc.seller.country_code)
        _el(id_trasmittente, "IdPaese", paese_tx)
        if codice_tx:
            _el(id_trasmittente, "IdCodice", codice_tx)
        _el(trasmissione, "FormatoTrasmissione", _VERSIONE)
        _el(
            trasmissione,
            "CodiceDestinatario",
            doc.buyer_reference or _DEFAULT_CODICE_DESTINATARIO,
        )

        cedente = _el(header, "CedentePrestatore")
        self._build_anagrafici(cedente, doc.seller)

        cessionario = _el(header, "CessionarioCommittente")
        self._build_anagrafici(cessionario, doc.buyer)

        # --- Body ---
        body = _el(root, "FatturaElettronicaBody")

        generali = _el(body, "DatiGenerali")
        documento = _el(generali, "DatiGeneraliDocumento")
        tipo = _INVOICE_TYPE_TO_TIPO_DOCUMENTO.get(
            doc.invoice_type_code or "", _DEFAULT_TIPO_DOCUMENTO
        )
        _el(documento, "TipoDocumento", tipo)
        if doc.currency:
            _el(documento, "Divisa", doc.currency)
        if doc.issue_date is not None:
            _el(documento, "Data", doc.issue_date.isoformat())
        if doc.invoice_number:
            _el(documento, "Numero", doc.invoice_number)
        if doc.payable_amount is not None:
            _el(documento, "ImportoTotaleDocumento", _amount_text(doc.payable_amount))

        beni = _el(body, "DatiBeniServizi")
        for i, line in enumerate(doc.lines, start=1):
            dettaglio = _el(beni, "DettaglioLinee")
            _el(dettaglio, "NumeroLinea", line.line_id or str(i))
            if line.description:
                _el(dettaglio, "Descrizione", line.description)
            if line.quantity is not None:
                _el(dettaglio, "Quantita", _amount_text(line.quantity))
            if line.unit_price is not None:
                _el(dettaglio, "PrezzoUnitario", _amount_text(line.unit_price))
            if line.line_total is not None:
                _el(dettaglio, "PrezzoTotale", _amount_text(line.line_total))
            if line.tax_rate is not None:
                _el(dettaglio, "AliquotaIVA", _rate_text(line.tax_rate))

        riepilogo = _el(beni, "DatiRiepilogo")
        # One summary block; the rate is the document's first tax line's rate.
        if doc.taxes and doc.taxes[0].rate is not None:
            _el(riepilogo, "AliquotaIVA", _rate_text(doc.taxes[0].rate))
        if doc.tax_exclusive_amount is not None:
            _el(riepilogo, "ImponibileImporto", _amount_text(doc.tax_exclusive_amount))
        if doc.tax_total is not None:
            _el(riepilogo, "Imposta", _amount_text(doc.tax_total))

        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)

    # --------------------------------------------------------------- validate
    def validate(self, doc: EInvoiceDocument) -> list[FieldError]:
        errors = validate_document(doc, check_tax=False)

        # FatturaPA mandates both the cedente (seller) and the cessionario
        # (buyer) carry a Partita IVA / Codice Fiscale.
        for path, party in (("seller", doc.seller), ("buyer", doc.buyer)):
            if not party.tax_id:
                errors.append(
                    FieldError(
                        f"{path}.tax_id",
                        "missing",
                        "FatturaPA requires a Partita IVA / Codice Fiscale for this party",
                    )
                )
            elif validate_tax_id("IT", party.tax_id):
                errors.append(
                    FieldError(
                        f"{path}.tax_id",
                        "malformed",
                        "Partita IVA format is invalid for Italy",
                    )
                )

        if doc.payable_amount is None:
            errors.append(
                FieldError(
                    "payable_amount",
                    "missing",
                    "FatturaPA requires the document total (ImportoTotaleDocumento)",
                )
            )

        return errors
