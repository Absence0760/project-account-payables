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
| `validate.py` | `validate_document(doc, *, check_tax=True)` / `assert_valid` + `FieldError` + `EInvoiceValidationError`. EN 16931-subset structural checks; appends `tax_rules.validate_tax_document(doc)` when `check_tax`. |
| `parse.py` | `parse_e_invoice(file_bytes, mime_type, filename)` orchestrator: detect → embedded-extract → parse → assert_valid. |
| `generate.py` | `generate_ubl(doc) -> bytes`. Outbound UBL 2.1 serializer — the exact inverse of `ubl.py`. lxml etree, money via `Decimal.quantize`, `currencyID` on amounts. |
| `generate_cii.py` | `generate_cii(doc) -> bytes`. Outbound UN/CEFACT CII (D16B) serializer — the exact inverse of `cii.py`. Emits `rsm:CrossIndustryInvoice` with the `ram:`/`udt:` namespaces; same lxml-etree posture as `generate.py` (Decimal money, escaped text); dates as the CII basic-date form (`format="102"` → `YYYYMMDD`). |
| `mapper.py` | `BuyerIdentity` dataclass + `invoice_to_einvoice_document(invoice, line_items, buyer_identity) -> EInvoiceDocument`. Pure ORM `Invoice` → normalized model. |
| `payment_means.py` | The UNCL4461 code ⇄ `Invoice.payment_method` token table, **both directions in one place** so inbound (`einvoice_adapter`) and outbound (`mapper`) can't drift. `payment_means_to_method` / `method_to_payment_means`; an unmappable token yields `None` and the optional element is omitted rather than carrying an out-of-code-list value. |
| `tax_rules.py` | Country tax validation: `validate_tax_id` / `validate_tax_rate` / `validate_tax_document(doc) -> list[FieldError]`. VAT/GST/IVA/CNPJ/NIT id formats + rate plausibility + zero-rate/reverse-charge. Shared by inbound (`validate.py`) and outbound (export route + national formats). |
| `country_formats/` | National outbound dialects — `base.py` (`CountryEInvoiceFormat` interface), `dispatcher.py` (registry), and `fatturapa.py` (IT) / `cfdi.py` (MX) / `nfe.py` (BR) / `dian.py` (CO). Generation + national validation only; live clearance deferred. See § National e-invoice formats. |

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
`check`, `48`/`54` → `credit_card`, else None. It lives in
`e_invoice/payment_means.py` — **both** directions in one table, because the
outbound mapper needs the inverse and the two must not drift. Outbound emits
`ach` → `30`, `wire` → `42`, `check` → `20`, `credit_card` → `48`; `other` (and
anything unrecognised) yields `None` and the optional element is **omitted** —
a document with no payment means is valid, one carrying an out-of-code-list
value is not. A value that is already a UNCL4461 code passes through unchanged
(`Invoice.payment_method` is a free-form `String(50)` an API client can write
directly). `tests/test_e_invoice_payment_means.py` pins
`payment_means_to_method(method_to_payment_means(m)) == m` for every token.

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

## Outbound UBL 2.1 + CII generation

The `EInvoiceDocument` model is a full bidirectional representation, so outbound
generation reuses it as-is — **no model change**. Pure modules (no DB, no
network, on by default, no new dependency):

### `generate_ubl(doc) -> bytes`

Serializes an `EInvoiceDocument` to UBL 2.1 Invoice XML (UTF-8 + declaration).
It is the exact inverse of `ubl.py`'s parser: same root
(`...:xsd:Invoice-2`), same `cbc:` / `cac:` namespaces, same element order.
Built with `lxml.etree` (etree escapes text nodes automatically, so a vendor
name with `<`, `&`, `>` can never inject markup — no string templating). Money
is serialized via `Decimal.quantize` (amounts 2dp, quantities 4dp) — never
`float` — and every monetary element carries `currencyID = doc.currency`.
Optional fields are omitted when `None`, so the round-trip property holds:

```
parse_ubl(generate_ubl(doc)) == doc   # on every core field
```

### `generate_cii(doc) -> bytes`

Serializes an `EInvoiceDocument` to UN/CEFACT CII (Cross-Industry Invoice, D16B)
XML — the dialect Factur-X / ZUGFeRD embed in a PDF/A-3 (NOT UBL). It is the
exact inverse of `cii.py`'s parser: same root (`rsm:CrossIndustryInvoice`), same
`ram:` / `udt:` namespaces, same element order — `rsm:ExchangedDocument` header +
`rsm:SupplyChainTradeTransaction` (line items, then the three header aggregates:
`ApplicableHeaderTradeAgreement` parties+order-ref / `ApplicableHeaderTradeDelivery`
/ `ApplicableHeaderTradeSettlement` currency+payment+tax+monetary-summation).
Same lxml-etree posture as `generate_ubl` (text escaped, no templating; money via
`Decimal.quantize`, amounts 2dp / quantities 4dp); dates emit as the CII
basic-date form (`format="102"` → `YYYYMMDD`) that the parser's `to_date` reads
back. Optional fields are omitted when `None`, so the round-trip property holds:

```
parse_cii(generate_cii(doc)) == doc   # on every core field
```

### `invoice_to_einvoice_document(invoice, line_items, buyer_identity)`

Pure mapper from an ORM `Invoice` (+ its `InvoiceLineItem` rows + our identity)
into the normalized model. The **seller** is the vendor (`vendor_name` /
`vendor_tax_id` / `vendor_address` split on newline into address lines); the
**buyer** (AccountingCustomerParty = us) is filled from `BuyerIdentity` — a
dataclass that lives in `mapper.py`, *not* on the model. The seller's
`country_code` is **derived from the VAT-id prefix** (`DE…` → DE, `FR…` → FR,
Greece's `EL…` → GR, `GB…` → GB) because the `Invoice` row has no vendor-country
column — this is what lets the outbound export guard validate the supplier's
tax-id format and rate plausibility (not just the buyer side). A non-prefixed
scheme (US EIN, AU ABN, bare number) leaves `country_code` `None`, and the
tax-rule validators then skip the seller side. Totals map as:
`line_extension_amount`/`tax_exclusive_amount` ← `subtotal`,
`tax_inclusive_amount`/`payable_amount` ← `amount`, `tax_total` ← `tax_amount`,
`allowance_total` ← `discount_amount`, `charge_total` ← `shipping_amount`. One
`EInvoiceTax` is built from `(tax_rate, subtotal, tax_amount)` when tax is set.
Each line carries its own `tax_amount` ← `InvoiceLineItem.tax`, emitted by BOTH
generators (`cac:TaxTotal/cbc:TaxAmount` in UBL,
`ram:ApplicableTradeTax/ram:CalculatedAmount` in CII) and read back by both
parsers. `payment_means_code` is the mapped UNCL4461 **code**, never the
internal `payment_method` token (see the field map above). Decimal is preserved
end to end.

### Routes

| Route | Auth | Behaviour |
|-------|------|-----------|
| `GET /api/invoices/{id}/einvoice?format=ubl` | employee JWT + `require_roles(admin, ap_manager, cfo, ap_clerk)`, `get_tenant_db` + `get_tenant` | Maps the tenant invoice → doc, resolves `BuyerIdentity` from `org.settings["company"]` (+ `Entity.name` override when `invoice.entity_id` is set), **asserts tax-valid (422 on failure** — an AP user must not emit a non-compliant invoice; body is the PII-free `field: code` join), then returns the e-invoice as an `application/xml` attachment. Unknown invoice → 404. `format` selects the dialect: `ubl` (default) or `cii` (UN/CEFACT CII) take the **built-in** path (shared normalized model + the same `assert_valid` tax guard, only the generator differs); `fatturapa` (IT) / `cfdi` (MX) / `nfe` (BR) / `dian` (CO) select a **national format** via the country-format registry (see below); an unknown token → 400. The `ubl` download is `einvoice-<n>.xml`; every other dialect is format-tagged (`einvoice-<n>-<format>.xml`) so they don't collide. National exports validate via the format's own `validate(doc)` (422 PII-free on failure). |
| `GET /portal/invoices/{id}/einvoice` | vendor JWT (`get_current_vendor_user`) | **Vendor-scoped**: the query is `WHERE Invoice.id == id AND Invoice.vendor_id == vu.vendor_id` — a foreign or unknown invoice returns 404 (never a foreign document). Resolves the buyer `Organization` via the injected control session (`get_control_db`) by `invoice.organization_id` (same pattern as the remittance route). Does **not** 422 the supplier on a tax soft-warning — the UBL is always returned and any validation issue is logged field-only, never surfaced to the vendor. |

### Country tax validation (`tax_rules.py`)

A shared building block used in two postures. **Inbound** (`parse_e_invoice`) it
is **advisory only**: structural validation is strict (`assert_valid(doc,
check_tax=False)`), but country tax findings are logged field-only and never
raise — a vendor's legitimately-issued document with an unenumerated-but-valid
rate or a regex-rejected VAT id must not fail ingestion (the regexes carry no
checksums and the rate sets are a sanity band, not a full schedule).
**Outbound** (the authenticated export's `assert_valid` with the default
`check_tax=True`) it is a **hard pre-generation guard** (422) — there a human AP
user can fix the document before emission. `validate_document(doc,
check_tax=True)` is still available for any caller that wants the combined
result directly. Three checks, all PII-free (`FieldError` names the field path +
a code, never the value):

- **Tax-ID format** per country — every EU member-state VAT regex + GB VAT,
  AU ABN (11 digits), NZ/IN/CA GST, MX RFC, ES/IT IVA (their EU VAT regex). An
  **unknown / unsupported country code is skipped** (inbound documents arrive
  from any country); only a *known* country with a *malformed* id fails.
- **Tax-rate plausibility** per regime — a rate outside the country's standard
  + reduced set (with a 0.01 tolerance) is `implausible`.
- **Reverse-charge / zero-rate** — categories `Z` (zero-rated) and `AE`/`E`
  (reverse-charge / exempt) are plausible at `0.00`, so a legitimate 0% line is
  never flagged.

| Regime | Countries | Tax-ID example |
|--------|-----------|----------------|
| VAT (EU) | AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK | `DE123456789`, `FR40123456789`, `NL123456789B01` |
| VAT (UK) | GB | `GB123456789` |
| GST / ABN | AU NZ IN CA | `12345678901` (ABN), `29ABCDE1234F1Z5` (GSTIN) |
| IVA | ES IT MX | `ESA12345674`, `IT12345678901`, `ABCD901231XYZ` (RFC) |
| CNPJ / NIT | BR CO | `12345678000195` (CNPJ, 14 digits), `900123456` (NIT, 9–10 digits) |

## National e-invoice formats (`country_formats/`)

Beyond UBL 2.1, several jurisdictions mandate their **own** national XML dialect
for the cleared / fiscalized invoice. Each is a small, **pure, local-first**
`CountryEInvoiceFormat` (generation + national validation only) registered under
a `format_code` and resolved by the export route's `?format=` parameter.

| `format_code` | Country | Dialect emitted | Validation |
|---------------|---------|-----------------|------------|
| `fatturapa` | IT | `FatturaElettronica` v1.2 (`FPR12`) — `…Header` (DatiTrasmissione + CedentePrestatore/CessionarioCommittente) + `…Body` (DatiGeneraliDocumento, DatiBeniServizi, DatiRiepilogo). `IdFiscaleIVA` / `IdTrasmittente` are **two-part** ids: `IdPaese` carries the ISO country and `IdCodice` the VAT number *without* it, so `_split_id_fiscale` strips the country prefix the normalized model stores (`IT12345678901` → `IT` + `12345678901`; Greece's `EL` maps to ISO `GR`). A non-prefixed id, or a prefix that doesn't identify the emitted country, passes through untouched. | seller **and** buyer Partita IVA required + IT-format; `payable_amount` |
| `cfdi` | MX | `cfdi:Comprobante` v4.0 — Emisor / Receptor (RFC) + Conceptos + Impuestos. `Concepto/@ClaveProdServ` is a SAT `c_ClaveProdServ` **catalog key**, so it stays the documented `01010101` "not in the catalog" placeholder; the line's `item_code` (the *seller's* part number) goes to `@NoIdentificacion`, the attribute CFDI provides for exactly that. | emisor **and** receptor RFC required + MX-format; `payable_amount`, `tax_exclusive_amount` |
| `nfe` | BR | `NFe/infNFe` v4.00 — ide / emit / dest / det·prod / total·ICMSTot | emit CNPJ required + BR-format; `payable_amount` |
| `dian` | CO | DIAN-profiled UBL 2.1 (`CustomizationID=10`, `ProfileID="DIAN 2.1…"`, `UBLExtensions` placeholder) | supplier NIT required + CO-format; `payable_amount` |

**Architecture** — `country_formats/base.py` defines the `CountryEInvoiceFormat`
interface (`format_code` / `country` / `display_name` / `file_extension` /
`media_type` + `validate(doc)` + `generate(doc)`); `dispatcher.py` is the
registry (`@register_country_format(code)` / `get_country_format(code)` /
`list_country_formats()`), mirroring the PEPPOL / payment adapter dispatchers.
Importing `country_formats` self-registers all four. The generators reuse the
shared structural validation (`validate_document(doc, check_tax=False)`) and the
`tax_rules.validate_tax_id` country regexes (BR/CO added alongside the existing
EU/UK/MX set), build XML with `lxml.etree` (auto-escaping), and keep money as
`Decimal.quantize` → `str` (never `float`).

**Scope — what ships vs. deferred.** This slice is everything that needs **no
cloud account**: the pre-clearance national document + its structural/tax
validation, wired into the authenticated export route. **Live government
clearance is deliberately out of this slice** and tracked in
`../../docs/roadmap.md` → Automated E-Invoicing — it slots in behind the same
registry as a future adapter (exactly as PEPPOL's `as4_gateway` followed the
`mock` default):

- **FatturaPA** — SdI transmission + the `.p7m` (CAdES) digital signature.
- **CFDI 4.0** — SAT-PAC stamping → `Sello` / `Certificado` / the
  `tfd:TimbreFiscalDigital` UUID (folio fiscal).
- **NF-e** — SEFAZ authorization → the 44-digit *chave de acesso* + *protocolo*
  + digital signature (a deterministic placeholder `Id` is emitted meanwhile).
  Municipal **NFS-e** is a separate per-municipality schema, also future scope.
- **DIAN** — the CUFE (código único de factura electrónica) + XAdES signature +
  the `dian:DianExtensions` block, injected into the emitted `UBLExtensions`
  placeholder at clearance.
