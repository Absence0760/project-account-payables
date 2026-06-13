"""Orchestrate detection → parse → validate for an inbound file.

Single entry point the ``einvoice`` extraction adapter calls. Detection runs
first (so a non-structured file raises ``ValueError`` cleanly and the caller
falls back to vision), then the format-specific parser, then *structural*
validation (which raises :class:`EInvoiceValidationError` on a malformed or
incomplete structured document). Country tax rules run here too but only as
**advisory** warnings — they never reject a vendor's document (that strictness
is reserved for the outbound export guard).
"""

from __future__ import annotations

import logging

from lxml.etree import XMLSyntaxError

from app.services.e_invoice.cii import parse_cii
from app.services.e_invoice.detect import DetectedFormat, detect_format
from app.services.e_invoice.facturx import extract_embedded_cii_xml
from app.services.e_invoice.model import EInvoiceDocument
from app.services.e_invoice.tax_rules import validate_tax_document
from app.services.e_invoice.ubl import parse_ubl
from app.services.e_invoice.validate import EInvoiceValidationError, FieldError, assert_valid

logger = logging.getLogger(__name__)


def parse_e_invoice(
    file_bytes: bytes,
    mime_type: str | None = None,
    filename: str | None = None,
) -> EInvoiceDocument:
    """Detect, parse, and validate a structured e-invoice.

    Raises:
        ValueError: the file is not a structured e-invoice (detection NONE).
        EInvoiceValidationError: the file is structured but malformed or fails
            the EN 16931-subset *structural* checks. Country tax-rule findings
            (tax-id format, rate plausibility) are advisory only — logged
            field-only, never raised — so a legitimate foreign invoice is not
            rejected at ingestion.
    """
    fmt = detect_format(file_bytes, mime_type, filename)

    if fmt is DetectedFormat.NONE:
        raise ValueError("not a structured e-invoice")

    try:
        if fmt is DetectedFormat.UBL:
            doc = parse_ubl(file_bytes)
        elif fmt is DetectedFormat.CII_XML:
            doc = parse_cii(file_bytes)
        elif fmt is DetectedFormat.FACTURX_PDF:
            cii_bytes = extract_embedded_cii_xml(file_bytes)
            if not cii_bytes:
                # detect said FACTURX_PDF, so this is a race / corruption.
                raise EInvoiceValidationError(
                    [FieldError("document", "malformed", "Embedded CII XML could not be read")]
                )
            doc = parse_cii(cii_bytes)
        else:  # pragma: no cover - exhaustive guard
            raise ValueError("not a structured e-invoice")
    except XMLSyntaxError as exc:
        raise EInvoiceValidationError(
            [FieldError("document", "malformed", "XML could not be parsed")]
        ) from exc

    # Structural validation is strict on the inbound path, but country tax
    # rules (tax-id FORMAT + rate plausibility) are NOT a reason to reject a
    # vendor's legitimately-issued document — those regexes carry no checksums
    # and the rate sets are a sanity band, not a full schedule (see tax_rules
    # docstring). They are an *outbound* pre-emission guard where a human AP
    # user can fix the document, not an inbound ingestion gate. Surface any
    # country tax findings as advisory, field-only warnings (no PII, no value)
    # rather than a hard EInvoiceValidationError.
    assert_valid(doc, check_tax=False)

    tax_findings = validate_tax_document(doc)
    if tax_findings:
        logger.info(
            "inbound e-invoice has advisory tax warnings: %s",
            "; ".join(f"{e.field}: {e.code}" for e in tax_findings),
        )

    return doc
