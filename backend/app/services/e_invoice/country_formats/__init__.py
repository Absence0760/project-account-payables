"""Country-specific outbound e-invoice formats (national XML dialects).

The shared UBL 2.1 generator (:mod:`app.services.e_invoice.generate`) covers
the PEPPOL / EN 16931 corridor. Several jurisdictions mandate their own
national format for the cleared invoice; each is a small, pure, local-first
:class:`~app.services.e_invoice.country_formats.base.CountryEInvoiceFormat`
(generation + national validation only — live government clearance is a tracked
follow-up). Importing the modules below self-registers them under their
``format_code`` so :func:`get_country_format` resolves them by the export
route's ``?format=`` parameter.

Registered: ``fatturapa`` (IT), ``cfdi`` (MX), ``nfe`` (BR), ``dian`` (CO).
"""

# Import the format modules so their @register_country_format decorators run.
from app.services.e_invoice.country_formats import cfdi as _cfdi  # noqa: F401
from app.services.e_invoice.country_formats import dian as _dian  # noqa: F401
from app.services.e_invoice.country_formats import fatturapa as _fatturapa  # noqa: F401
from app.services.e_invoice.country_formats import nfe as _nfe  # noqa: F401
from app.services.e_invoice.country_formats.base import CountryEInvoiceFormat
from app.services.e_invoice.country_formats.dispatcher import (
    get_country_format,
    list_country_formats,
    register_country_format,
)

__all__ = [
    "CountryEInvoiceFormat",
    "get_country_format",
    "list_country_formats",
    "register_country_format",
]
