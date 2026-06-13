"""Inbound structured e-invoice ingestion (UBL 2.1 + UN/CEFACT CII / Factur-X).

Pure, local-first, no-network parsing of machine-readable invoices:

- **UBL 2.1** — PEPPOL BIS Billing 3.0 (standalone XML).
- **UN/CEFACT CII** — the dialect Factur-X / ZUGFeRD embed inside a PDF/A-3.

``parse_e_invoice`` is the orchestrator the ``einvoice`` extraction adapter
runs. ``detect_format`` is the routing decision point ``extraction.run_extraction``
calls after fetching the file bytes. The normalized :class:`EInvoiceDocument`
is deliberately bidirectional so a future outbound-generation slice can render
UBL from the same model.
"""

from __future__ import annotations

from app.services.e_invoice.cii import parse_cii
from app.services.e_invoice.detect import DetectedFormat, detect_format
from app.services.e_invoice.facturx import extract_embedded_cii_xml
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)
from app.services.e_invoice.parse import parse_e_invoice
from app.services.e_invoice.ubl import parse_ubl
from app.services.e_invoice.validate import (
    EInvoiceValidationError,
    FieldError,
    assert_valid,
    validate_document,
)

__all__ = [
    "EInvoiceDocument",
    "EInvoiceParty",
    "EInvoiceLine",
    "EInvoiceTax",
    "EInvoiceFormat",
    "DetectedFormat",
    "detect_format",
    "parse_ubl",
    "parse_cii",
    "extract_embedded_cii_xml",
    "validate_document",
    "assert_valid",
    "EInvoiceValidationError",
    "FieldError",
    "parse_e_invoice",
]
