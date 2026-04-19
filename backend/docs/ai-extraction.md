# AI Invoice Extraction

## Overview

When an invoice is uploaded, the system extracts all structured data (vendor, amount, dates, line items) using AI/OCR. The extraction service supports two models:

| Model | How it works | Cost | Who pays for AI |
|---|---|---|---|
| **Platform** (default) | Uses your Claude Vision API key | Per-extraction fee to customer | You |
| **BYOK** (Bring Your Own Key) | Customer provides their own AI API key | Free (no extraction fee) | Customer |

## Supported Providers

| Provider | Type | PDF Support | Best for |
|---|---|---|---|
| **Claude Vision** (Anthropic) | Platform default + BYOK | Native (document mode) | Highest accuracy, structured output |
| **GPT-4V** (OpenAI) | BYOK only | Text extraction + image fallback | Customers already on OpenAI |
| **AWS Textract** | BYOK only | Native | Customers on AWS with AnalyzeExpense |
| **Ollama** (Local) | BYOK only | Text extraction + image fallback | Development, privacy, on-premise |
| **Mock** | Development | N/A | Testing without API calls |

## PDF Handling: Smart Text vs Vision

Most invoices from modern systems are digital PDFs with a text layer — they don't need OCR at all. The system detects this automatically:

```
PDF Uploaded
    |
    v
Extract text from PDF (PyMuPDF)
    |
    ├── Has text layer (> 50 chars)?
    │       |
    │       v
    │   TEXT MODE — send extracted text to AI model
    │   - Works with ANY model (no vision model needed)
    │   - Faster (instant text extraction vs 10-30s vision)
    │   - Cheaper (text tokens vs image tokens)
    │   - More accurate (no OCR errors)
    │   - Even works with text-only models (qwen, llama, etc.)
    │
    └── No text layer (scanned/photographed)?
            |
            v
        IMAGE MODE — convert PDF page to PNG at 200 DPI
        - Requires a vision model (LLaVA, Llama 3.2 Vision, Claude, GPT-4V)
        - Slower, more expensive
        - Less accurate (OCR quality depends on scan quality)
```

**What this means per provider:**

| Provider | Text PDFs | Scanned PDFs | Images (PNG/JPG) |
|---|---|---|---|
| **Claude Vision** | Native document mode | Native document mode | Native vision |
| **Ollama** | Text extraction → any model | PDF → image → vision model | Direct vision |
| **GPT-4V** | Text extraction → text mode | PDF → image → vision mode | Direct vision |
| **AWS Textract** | Native | Native | Native |

**For Ollama specifically:** Digital PDFs work with any Ollama model — even text-only models like `qwen2.5-coder` or `llama3`. You only need a vision model (`llama3.2-vision`, `llava`) for scanned invoices or photos.

### Requirements

PDF text extraction and image conversion require PyMuPDF:

```bash
cd backend && .venv/bin/pip install -e ".[dev]"
```

PyMuPDF is included in the project dependencies (`pyproject.toml`).

## Extraction Flow

```
Invoice Uploaded
    |
    v
Dispatch (local or Lambda)
    |
    v
Resolve Config
    ├── Platform → use app-level Anthropic key (AP_ANTHROPIC_API_KEY)
    └── BYOK → use customer's key from org.settings.extraction
    |
    v
Adapter.extract(file_url, file_key, mime_type)
    |
    v
Parse Response → ExtractionResult (per-field confidence)
    |
    v
Apply to Invoice
    ├── Header fields (vendor, amount, dates, etc.)
    ├── Line items
    ├── AI GL coding (suggest GL account + cost center)
    └── Vendor matching (fuzzy match or auto-create)
    |
    v
Track Usage (ExtractionUsage record)
    |
    v
Transition: pending → ready_for_review
```

## Per-Field Confidence

Every extracted field includes a confidence score (0.0 to 1.0). The extraction prompt asks the AI to self-rate certainty per field.

| Confidence | Treatment |
|---|---|
| >= 0.9 | Auto-applied, no flag |
| 0.7 - 0.9 | Applied, flagged for review |
| < 0.7 | Not auto-applied, shown as suggestion |

GL coding uses the 0.7 threshold — only auto-applied when the AI is reasonably confident.

## AI GL Coding

The extraction prompt includes GL account suggestions based on the vendor type and invoice description:

| Category | Suggested GL |
|---|---|
| Office supplies | 6100 |
| Cloud/software | 6200 |
| Facility/maintenance | 6300 |
| Marketing | 6400 |
| Legal/professional | 6500 |
| Food/catering | 6600 |
| Shipping/logistics | 6700 |
| Hardware/equipment | 1500 |

The org's chart of accounts can be included in the prompt (future improvement) for more accurate suggestions.

## ExtractionResult Structure

```python
ExtractionResult:
    success: bool
    overall_confidence: float  # average of key fields
    
    # Header fields — each is ExtractedField(value, confidence)
    invoice_number, vendor_name, vendor_address, vendor_tax_id,
    amount, currency, subtotal, tax_amount, tax_rate,
    discount_amount, shipping_amount, invoice_date, due_date,
    payment_terms, po_number, description, reference_number,
    payment_method, bill_to_address, remit_to_address
    
    # AI suggestions
    suggested_gl_account, suggested_cost_center
    
    # Line items
    line_items: list[ExtractedLineItem]
    
    # Debug
    raw_response, provider, error
```

## Usage Tracking & Billing

Every extraction (success or failure) creates an `ExtractionUsage` record:

| Field | Description |
|---|---|
| invoice_id | Which invoice was extracted |
| provider | Which AI provider was used |
| program_type | "platform" or "byok" |
| period | "2026-04" (for monthly billing) |
| success | Whether extraction succeeded |
| organization_id | Tenant |

**Billing logic:**
- Platform extractions are billable (you charge the customer)
- BYOK extractions are free (customer pays their own AI provider)
- Failed extractions are tracked but not billable

Query for monthly billing:
```sql
SELECT organization_id, period, count(*) as extractions
FROM extraction_usage
WHERE program_type = 'platform' AND success = true
GROUP BY organization_id, period;
```

## Organization Settings

Stored in `Organization.settings.extraction`:

```json
{
  "extraction": {
    "program_type": "platform",
    "provider": "claude_vision"
  }
}
```

BYOK example:
```json
{
  "extraction": {
    "program_type": "byok",
    "provider": "openai_vision",
    "api_key": "sk-..."
  }
}
```

Platform mode uses environment variables:
- `AP_ANTHROPIC_API_KEY` — your Anthropic API key
- `AP_EXTRACTION_MODEL` — model to use (default: claude-sonnet-4-20250514)

## Code Structure

```
backend/app/services/extraction_adapters/
    __init__.py              # Package exports
    base.py                  # ExtractionAdapter, ExtractionResult, ExtractedField types
    dispatcher.py            # get_extraction_adapter() — picks adapter from config
    mock_adapter.py          # Dev/testing
    claude_vision.py         # Claude Vision (platform default)
    openai_vision.py         # GPT-4V (BYOK)
    aws_textract.py          # AWS Textract (BYOK)

backend/app/services/extraction.py       # Orchestrates extraction + vendor matching + GL coding
backend/app/services/extraction_dispatch.py  # Routes to local or Lambda
backend/app/models/usage.py              # ExtractionUsage model for billing
```

## Implementation Status

| Feature | Status |
|---|---|
| Extraction adapter interface | Done |
| Mock adapter | Done |
| Claude Vision adapter (platform) | Done |
| OpenAI GPT-4V adapter (BYOK) | Done |
| AWS Textract adapter (BYOK) | Done |
| Per-field confidence scoring | Done |
| AI GL coding in extraction prompt | Done |
| Vendor matching after extraction | Done |
| Line item extraction | Done |
| Usage tracking (ExtractionUsage model) | Done |
| Extraction config in org settings UI | Done |
| Platform/BYOK dual model | Done |
| Usage billing dashboard | Planned |
| Custom chart of accounts in prompt | Planned |
| Multi-page PDF support | Planned |
| Learning from corrections — per-vendor cache | **Done** |
| Learning from corrections — RAG with pgvector | Planned |

---

## Learning from corrections — per-vendor cache

When reviewers fix extracted fields during approval, those corrections are stored against the matched vendor and reused on the next extraction. Deterministic, no ML, no cold-start cost — it handles the "same vendor's invoices follow the same template" case with a small tenant-scoped table.

### Data model

Tenant-scoped table `vendor_extraction_priors` with one row per `(vendor_id, field_name)`:

| Column | Type | Purpose |
|---|---|---|
| `id` | UUID | PK |
| `vendor_id` | UUID FK → vendors | Owner |
| `field_name` | VARCHAR(60) | e.g. `currency`, `payment_terms` |
| `value` | TEXT | Most-recent corrected value |
| `correction_count` | INT | Times this field has been corrected for this vendor |
| `last_applied_at` | TIMESTAMPTZ | Bumped each time a future extraction uses it |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

Unique on `(vendor_id, field_name)`. Model at `app/models/vendor_priors.py`.

### Cacheable fields (whitelist)

Only fields that are *vendor-consistent* are cached — fields that vary per-invoice are never stored:

```python
CACHEABLE_FIELDS = {
    "currency", "tax_rate", "payment_terms", "payment_method",
    "vendor_address", "vendor_tax_id", "remit_to_address",
    "gl_account", "cost_center",
}
```

Never cached: `invoice_number`, `amount`, `subtotal`, `tax_amount`, `discount_amount`, `shipping_amount`, `invoice_date`, `due_date`, `po_number`, `reference_number`, `description`, `line_items`.

### Write path — record on correction

`services.review.approve_invoice` accepts `corrections: dict | None`. Before committing, `services.vendor_priors.record_corrections` upserts each whitelisted field into `vendor_extraction_priors` and increments `correction_count`.

### Read path — apply on extraction

After `services.extraction.run_extraction` runs the adapter and `match_and_link_vendor` links the invoice to a known vendor, `services.vendor_priors.apply_priors_to_invoice` overlays cached values on *low-confidence* extracted fields only (threshold: 0.8 by default). The extraction adapter still runs on the raw file — priors are a post-extraction correction, not a prompt hint.

This keeps the adapter contract simple and avoids the chicken-and-egg of "need vendor to fetch priors, but priors live in prompt." The tradeoff: the AI doesn't see priors as hints, so it can't reconcile a contradictory invoice against them. For the vendor-template-consistency use case that's fine.

### Cold-start / small-tenant behavior

First invoice from a vendor: no priors exist, no overlay, identical behavior to before. After one correction, that field is cached for the vendor from then on.
