"""Base interface for country-specific e-invoice formats (outbound generation).

The UBL 2.1 generator in :mod:`app.services.e_invoice.generate` covers the
pan-European / PEPPOL corridor. Several jurisdictions mandate their *own*
national XML dialect for the cleared / fiscalized invoice:

* **FatturaPA** — Italy (Sistema di Interscambio, SdI)
* **CFDI 4.0** — Mexico (SAT, PAC stamping → UUID / folio fiscal)
* **NF-e / NFS-e** — Brazil (state SEFAZ, chave de acesso)
* **DIAN** — Colombia (UBL-based, CUFE)

Each format is a small, **pure, local-first** unit that renders the shared
:class:`~app.services.e_invoice.model.EInvoiceDocument` into the national XML
and validates the country-specific structural + tax-id rules *before*
emitting (so an AP user can never export a malformed national document).

Live *clearance* — transmitting to the SdI / SAT-PAC / SEFAZ / DIAN and
receiving the government authorization id — is a networked government
integration and is deliberately **out of this slice** (tracked in
``docs/roadmap.md`` → Automated E-Invoicing); it slots in behind the same
registry as a future adapter, exactly as PEPPOL's ``as4_gateway`` did. What
ships here is everything that can be done with no cloud account: generation +
national validation, wired into the export route.

PII invariant (identical to the UBL generator): the generated XML legitimately
carries seller/buyer tax ids, addresses, and emails — that is the document's
purpose — but a :class:`~app.services.e_invoice.validate.FieldError` names the
*field path* + a generic code only, and no value from the document ever enters
a log line or an HTTP error body.
"""

from __future__ import annotations

from app.services.e_invoice.model import EInvoiceDocument
from app.services.e_invoice.validate import FieldError


class CountryEInvoiceFormat:
    """Base class for a national e-invoice format generator + validator.

    Subclasses are stateless and pure — no DB, no network, no clock. They are
    registered under their :attr:`format_code` via
    :func:`~app.services.e_invoice.country_formats.dispatcher.register_country_format`
    and resolved by the export route from the ``?format=`` query parameter.
    """

    format_code: str = "base"  # the ``?format=`` token, e.g. "fatturapa"
    country: str = ""  # ISO 3166-1 alpha-2, e.g. "IT"
    display_name: str = ""  # human label, e.g. "FatturaPA (Italy)"
    file_extension: str = "xml"  # download filename extension
    media_type: str = "application/xml"  # Content-Type for the download

    def validate(self, doc: EInvoiceDocument) -> list[FieldError]:
        """Return country-specific problems; empty list means valid.

        Implementations layer the national rules on top of the shared
        structural checks in :mod:`app.services.e_invoice.validate`. Every
        :class:`FieldError` is PII-free (names the field path + a code, never
        the value).
        """
        raise NotImplementedError

    def generate(self, doc: EInvoiceDocument) -> bytes:
        """Serialize ``doc`` to the national XML dialect (UTF-8 bytes).

        Built with ``lxml.etree`` (the package's XML engine — etree escapes
        text nodes, so a value containing ``<`` / ``&`` can never inject
        markup) and money via :meth:`Decimal.quantize` + ``str`` — never
        ``float``.
        """
        raise NotImplementedError
