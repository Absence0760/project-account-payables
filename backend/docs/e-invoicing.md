# Inbound structured e-invoice ingestion (UBL / Factur-X / ZUGFeRD)

Structured (machine-readable) invoices are parsed deterministically — no LLM,
no network, no API key — and routed automatically on ingest. The feature is
**on by default** and fully local-first: `pnpm dev` ingests a UBL or Factur-X
file out of the box.

## Formats supported

| Format | Carrier | XML dialect | Detected as |
|--------|---------|-------------|-------------|
| **UBL 2.1** (PEPPOL BIS Billing 3.0) | standalone `.xml` | UBL (`cbc:`/`cac:`) | `UBL` |
| **UN/CEFACT CII** (D16B) | standalone `.xml` | CII (`rsm:`/`ram:`/`udt:`) | `CII_XML` |
| **Factur-X** (FR) / **ZUGFeRD** (DE) | PDF/A-3 with embedded XML | CII (embedded) | `FACTURX_PDF` |

Key domain fact: ZUGFeRD / Factur-X embed **CII**, not UBL — so inbound support
needs both a UBL parser *and* a CII parser, plus a PDF-embedded-XML extractor
for the hybrid format.

## Package: `app/services/e_invoice/`

| Module | Responsibility |
|--------|----------------|
| `model.py` | Normalized `EInvoiceDocument` + `EInvoiceParty` / `EInvoiceLine` / `EInvoiceTax` + `EInvoiceFormat`. All money/quantity fields `Decimal \| None`. Deliberately **bidirectional** so a future outbound-generation slice can render UBL from the same model. |
| `_xml.py` | Hardened lxml parser factory (`secure_parser` / `parse_secure`) + namespace-prefix-agnostic helpers (`find_path`, `find_text`, `find_all_local`, `to_decimal`, `to_date`). |
| `detect.py` | `detect_format(file_bytes, mime_type, filename) -> DetectedFormat`. Pure, never raises. |
| `ubl.py` | `parse_ubl(xml_bytes) -> EInvoiceDocument`. |
| `cii.py` | `parse_cii(xml_bytes) -> EInvoiceDocument`. |
| `facturx.py` | `extract_embedded_cii_xml(pdf_bytes) -> bytes \| None` via PyMuPDF embedded-file API. Never raises. |
| `validate.py` | `validate_document` / `assert_valid` + `FieldError` + `EInvoiceValidationError`. EN 16931-subset structural checks. |
| `parse.py` | `parse_e_invoice(file_bytes, mime_type, filename)` orchestrator: detect → embedded-extract → parse → assert_valid. |

## Auto-detect-on-ingest routing

Detection lives in `extraction.run_extraction`, **after** the S3 fetch — the
single choke point both upload (`POST /invoices/upload`) and email intake reach
via `dispatch_extraction`. The adapter dispatcher only sees config, never file
bytes, so detection cannot live there.

```
run_extraction
  → fetch file_bytes from S3
  → _detect_structured_format(file_bytes, file_key)
       ├─ NONE       → keep org's configured vision/mock adapter (fall-through)
       └─ UBL/CII/FX → override config.provider = "einvoice"
                        + pass the real mime (xml vs pdf) into adapter.extract
```

A plain scanned PDF (embedded-XML probe returns `None`) falls through to the
org's configured vision/OCR adapter unchanged.

## The `einvoice` extraction adapter

`extraction_adapters/einvoice_adapter.py`, registered as `"einvoice"`.

- Runs `parse_e_invoice` and maps `EInvoiceDocument → ExtractionResult`.
- Every **present** field is emitted at `confidence=1.0` (deterministic parse,
  not a probabilistic model); `overall_confidence=1.0`. This naturally trips the
  extraction step's `auto_approve_threshold` (default 0.95) for trusted
  machine-readable invoices — no special-casing.
- Field values are emitted as **strings** (Decimals stringified, dates
  isoformat'd, payment-means mapped to canonical tokens) so the existing
  `extraction._apply_extraction` cleaners (`_clean_decimal` / `_clean_date` /
  `_normalize_payment_method`) re-parse them. Money stays `Decimal` at the DB
  boundary via those cleaners — zero new persistence path.
- `test_connection()` returns `True` unconditionally (pure / local).

### Field map (document → ExtractionResult)

| ExtractionResult | EInvoiceDocument |
|------------------|------------------|
| `invoice_number` | `invoice_number` |
| `vendor_name` | `seller.name` |
| `vendor_tax_id` | `seller.tax_id` |
| `vendor_address` | joined `seller` address lines + city + postal + country |
| `amount` | `payable_amount` (else `tax_inclusive_amount`) |
| `currency` | `currency` |
| `subtotal` | `line_extension_amount` |
| `tax_amount` | `tax_total` |
| `tax_rate` | single distinct `taxes[].rate` (else None) |
| `discount_amount` | `allowance_total` |
| `shipping_amount` | `charge_total` |
| `invoice_date` | `issue_date.isoformat()` |
| `due_date` | `due_date.isoformat()` |
| `payment_terms` | `payment_terms_note` |
| `po_number` | `order_reference` |
| `reference_number` | `buyer_reference` |
| `payment_method` | UNCL4461 `payment_means_code` → `ach`/`wire`/`check`/`credit_card` |
| `bill_to_address` | joined `buyer` address |
| `line_items` | `lines[]` → `ExtractedLineItem` |

UNCL4461 payment-means map: `30`/`58` → `ach`, `31`/`42` → `wire`, `20` →
`check`, `48`/`54` → `credit_card`, else None.

## Validation (EN 16931 structural subset)

Required: `invoice_number`, `issue_date`, `currency` (3-char alpha),
`seller.name`, `buyer.name`, ≥ 1 line, a grand total (`payable_amount` or
`tax_inclusive_amount`). Consistency: `tax_inclusive == tax_exclusive +
tax_total` when all three present (Decimal compare, tolerance `0.01`).

A malformed structured invoice is **rejected with field errors** rather than
silently falling back to vision: `parse_e_invoice` raises
`EInvoiceValidationError`, the adapter returns `success=False` with the field
list, and `run_extraction`'s existing failure path records an
`extraction_failed` exception. Detection `NONE`, by contrast, is a clean
fall-through to vision (not an error).

## PII / security

- **Errors name fields only, never values.** A `FieldError` is
  `(field, code, message)` where `message` names the field path
  (`seller.tax_id: missing`) — never the tax id / bank detail / address /
  amount. Honours the project's PII-out-of-logs/errors invariant.
- **`raw_response` carries only `{e_invoice_format, root_tag}`** — no party tax
  ids or addresses.
- **XXE-hardened XML parsing.** The repo has no `defusedxml` dependency, so
  lxml is hardened explicitly: `resolve_entities=False`, `no_network=True`,
  `load_dtd=False`, `huge_tree=False` — blocking XXE / external-entity /
  billion-laughs, consistent with the existing `python3-saml` posture. The
  parser factory is centralized in `_xml.secure_parser()`. Regression coverage
  for real attack payloads (file-disclosure SYSTEM entity, billion-laughs,
  external parameter entity) lives in `tests/test_e_invoice_xxe_hardening.py`.
- **Non-finite amounts are rejected.** `Decimal()` accepts `"NaN"` /
  `"Infinity"` as valid, which would corrupt downstream payment math.
  `_xml.to_decimal` returns `None` for any non-finite value (`d.is_finite()`
  guard) and for locale-grouped values like `"1,200.00"`; exponential notation
  (`"1.5E+3"`) is a valid finite number and is preserved. Pinned in
  `tests/test_e_invoice_xml_helpers.py`.

## Dependencies

No new dependency. XML parsing reuses **lxml** (already pinned `>=5,<7`); PDF
embedded-file extraction reuses **PyMuPDF / fitz** (already pinned `>=1.27`).

## Storage + email intake

XML attachments are accepted on both ingest paths:
- `storage.ALLOWED_CONTENT_TYPES` and `email_intake._ALLOWED_CONTENT_TYPES`
  include `application/xml` + `text/xml` (25 MB cap unchanged).
- Factur-X / ZUGFeRD arrive as PDF and are already covered by `application/pdf`.

## Future: outbound UBL generation

The `EInvoiceDocument` model is intentionally a full bidirectional
representation (separate seller/buyer `EInvoiceParty`, structured taxes,
monetary-summation totals, UNCL type/means codes). The next slice can generate
UBL 2.1 from the same model without remodeling — the natural hook is a
`generate_ubl(doc) -> bytes` companion to `parse_ubl`.
