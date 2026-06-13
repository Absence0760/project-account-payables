"""Detect whether an uploaded file is a structured e-invoice.

Pure, no network, never raises. The single decision point for routing an
inbound file to the structured-parse path (``einvoice`` adapter) versus the
vision/OCR path. Called from ``extraction.run_extraction`` after the file
bytes are fetched — the only choke point both upload and email-intake reach.
"""

from __future__ import annotations

from enum import StrEnum

from app.services.e_invoice._xml import local_name, namespace_of, parse_secure
from app.services.e_invoice.facturx import extract_embedded_cii_xml

# UBL 2.1 Invoice namespace + root local name.
_UBL_INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
_UBL_ROOT = "Invoice"
# UN/CEFACT CII namespace fragment + root local name.
_CII_NS_FRAGMENT = "CrossIndustryInvoice"
_CII_ROOT = "CrossIndustryInvoice"

_XML_MIMES = {"application/xml", "text/xml"}


class DetectedFormat(StrEnum):
    UBL = "ubl"
    CII_XML = "cii_xml"
    FACTURX_PDF = "facturx_pdf"
    NONE = "none"


def _looks_like_pdf(file_bytes: bytes, mime_type: str | None) -> bool:
    # The %PDF magic is authoritative — the mime is only a hint and is often a
    # stale default (e.g. the adapter's mime_type="application/pdf" fallback)
    # even when the bytes are raw XML. Trust the bytes.
    if file_bytes[:5].startswith(b"%PDF"):
        return True
    # Honour an explicit PDF mime only when the bytes don't already look like XML.
    if (mime_type or "").lower() == "application/pdf":
        return not _looks_like_xml(file_bytes, None, None)
    return False


def _looks_like_xml(file_bytes: bytes, mime_type: str | None, filename: str | None) -> bool:
    if (mime_type or "").lower() in _XML_MIMES:
        return True
    if filename and filename.lower().endswith(".xml"):
        return True
    # Sniff the leading bytes: strip a UTF-8/UTF-16 BOM + leading whitespace.
    head = file_bytes[:512]
    for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
        if head.startswith(bom):
            head = head[len(bom) :]
            break
    head = head.lstrip()
    return head.startswith(b"<?xml") or head.startswith(b"<")


def _classify_xml_root(file_bytes: bytes) -> DetectedFormat:
    try:
        root = parse_secure(file_bytes)
    except Exception:
        return DetectedFormat.NONE
    name = local_name(root)
    ns = namespace_of(root) or ""
    if name == _UBL_ROOT and _UBL_INVOICE_NS in ns:
        return DetectedFormat.UBL
    if name == _CII_ROOT and _CII_NS_FRAGMENT in ns:
        return DetectedFormat.CII_XML
    return DetectedFormat.NONE


def detect_format(
    file_bytes: bytes,
    mime_type: str | None = None,
    filename: str | None = None,
) -> DetectedFormat:
    """Classify a file as UBL, CII XML, Factur-X PDF, or NONE.

    Never raises. Unknown / unstructured input → ``DetectedFormat.NONE`` (the
    caller then falls back to the vision/OCR adapter).
    """
    if not file_bytes:
        return DetectedFormat.NONE

    if _looks_like_pdf(file_bytes, mime_type):
        # Hybrid PDF? Probe for an embedded CII attachment. A plain scanned
        # PDF has none → NONE → vision fallback.
        embedded = extract_embedded_cii_xml(file_bytes)
        return DetectedFormat.FACTURX_PDF if embedded else DetectedFormat.NONE

    if _looks_like_xml(file_bytes, mime_type, filename):
        return _classify_xml_root(file_bytes)

    return DetectedFormat.NONE
