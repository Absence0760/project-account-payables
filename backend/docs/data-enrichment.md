# Intelligent Data Enrichment from Supplier History

Advisory enrichment derived **deterministically** from each tenant's *own*
historical invoice data. No external calls, no cloud key — runs on a laptop
with `pnpm dev` and the mock adapters (local-first invariant). Three surfaces,
two read-only endpoints, all suggestion-only / compute-on-read.

| Surface | What it does | Persists? |
|---|---|---|
| **Auto-fill** | Suggests the dominant historical `gl_account` / `cost_center` / `payment_terms` for a draft invoice's vendor | No — advisory |
| **Price variance** | Flags draft line items whose unit price deviates from the vendor's per-item historical median | No — returned inline |
| **Vendor scoring** | Accuracy + dispute (+ optional on-time) sub-scores → renormalized composite | No — compute-on-read |

Pure statistics live in `app/services/vendor_enrichment.py` (sync, DB-free,
unit-testable); the SQL + response shaping live in `app/api/enrichment.py`. All
money / price math is `Decimal`; every numeric serialises as a **string** on the
wire (never a float). No vendor PII (`tax_id`, `bank_details`, address) ever
enters a response or a log line.

## Sibling to `vendor_priors`, not an extension

`services/vendor_priors.py` is a **correction cache**: one row per
`(vendor, field)`, holding the most-recently corrected value, silently overlaid
onto low-confidence extractions during extraction. This module is a different
shape — **distribution statistics over many historical invoices**, surfaced as
advisory hints to a reviewer and **never written back onto the invoice**. The
two read overlapping vendor history but share no write path (`vendor_enrichment`
is pure read), so there is no coupling or write-contention. The cost is a second
pass over the vendor's invoices, acceptable for an on-demand reviewer endpoint
(bounded `LIMIT`).

## Auto-fill

`suggest_fields(history_rows, current)` → `list[FieldSuggestion]`.

- History = the vendor's **approved-or-beyond** invoices (a draft/rejected
  invoice's coding is unreviewed noise), newest first, `LIMIT 50`, excluding the
  draft itself.
- Per field (`gl_account`, `cost_center`, `payment_terms`): take the dominant
  non-null value, `confidence = (occurrences / sample_size) * 100`.
- **Suppression rules** (suggestion-only / non-destructive):
  - Skip if the draft already holds a non-empty value for that field — never
    propose overwriting what the reviewer / extraction already set.
  - Skip if `confidence < MIN_CONFIDENCE` (default `60.0` — the value must be the
    majority).
  - Skip if `sample_size < MIN_SAMPLE` (default `3`).
- Ties break by **most-recent occurrence** (history is newest-first), so output
  is deterministic across runs.
- We do **not** name-match a vendor-less draft — too loose; it could suggest
  another vendor's GL. A vendor-less draft returns empty arrays.

## Price variance

`detect_price_variance(draft_lines, history_lines)` → `list[PriceVarianceFlag]`.

- Item key: prefer `item_code` (`code:<lower>`), else a normalized description
  (`desc:<lower, whitespace-collapsed, edge-punctuation-stripped>`). An empty /
  None description with no code is **unkeyable** and skipped (no baseline).
- Baseline is keyed by **`(item_key, currency)`** — a draft line is only ever
  compared against same-currency history. A vendor that bills in both USD and
  EUR never has its USD line judged against an EUR-pooled median (that produced
  a bogus `delta_pct` + a false over/under flag). `currency` defaults to `USD`
  (the `Invoice.currency` default) when absent. A line with **no same-currency
  history** is skipped (N/A), exactly like one with too little history.
- Baseline = the **median** of the item's same-currency historical unit prices
  (robust to a single outlier), quantized to cents.
- Flag when `abs(delta_pct) >= PRICE_TOLERANCE_PCT` (default `15.0`), needing
  `>= PRICE_MIN_HISTORY` (default `2`) prior prices for that item.
- `severity = "warning"` when `abs(delta_pct) >= PRICE_ESCALATE_PCT` (default
  `30.0`), else `"info"`. `direction = "over" | "under"`.
- History line rows are capped at `PRICE_HISTORY_LIMIT` (default `500`).

**Why inline, not persisted (this slice):** `invoice_warnings.refresh_warnings`
is a *write* path that runs on every invoice mutation and raises `Exception`
rows. Wiring price-variance there is a heavier behavior change (mutation-path
coupling, exception-queue noise, dedup) than a read-only "suggestions for this
draft" endpoint warrants. The pure `detect_price_variance` is built to be called
from `refresh_warnings` unchanged when that follow-up is taken.

## Vendor performance scoring

`compute_vendor_score(...)` → `VendorScore` (composite + sub-scores). Each
sub-score is `0..100` or **N/A** (`None`, excluded from the composite).

### Accuracy — `(1 - correction_rate) * 100`
Over the vendor's approved-or-beyond invoices that carry an `invoice.approved`
audit row: `correction_rate` = fraction whose approval included field
corrections (`details.changes` non-empty — the same signal
`adaptive_workflows` uses). Counted per **distinct invoice**, not per audit
row — an invoice can carry several `invoice.approved` rows (a rejected →
re-approved cycle, or a voided payment returning it to `approved` and being
re-approved), and an invoice counts as "corrected" if **any** of its approvals
carried changes. `approved_count == 0` → N/A.

### Dispute — `(1 - exception_rate) * 100`
`exception_rate` = distinct vendor invoices that raised a vendor-facing
`Exception` (`po_mismatch`, `duplicate`, `fraud_flag`, `missing_data`) over the
vendor's total invoice count (any status, status-agnostic — friction that
*happened* counts). `total_invoices == 0` → N/A.

### On-time delivery — **N/A by default**
`PurchaseOrder` has **no expected / promised / due-date column**, and
`GoodsReceipt.received_date` has nothing on the PO side to compare against. So
on-time is honestly **N/A and excluded from the composite** by default — a
vendor with no comparable data must not be punished.

An **opt-in** due-date proxy (org flag `ontime_use_due_date_proxy`, default
`false`) approximates on-time as `received_date <= invoice.due_date` (GR → PO by
vendor → Invoice by `po_number`). This is a weak proxy (invoice due date is not
the delivery-promised date), so it ships disabled and never silently produces a
misleading score. Making on-time real needs a PO expected-date column — a
tracked follow-up below.

### Composite (renormalized over available sub-scores)
Weights `{accuracy: 0.4, dispute: 0.3, on_time: 0.3}`, renormalized over only
the non-N/A sub-scores so an N/A component drops out cleanly:
`composite = (Σ wᵢ·scoreᵢ for available) / (Σ wᵢ for available)`. With on-time
N/A (the default), `composite = (0.4·accuracy + 0.3·dispute) / 0.7`. `None` when
no sub-score is available.

### Missing-data handling

| Situation | Result |
|---|---|
| Vendor with no invoices at all | all sub-scores N/A, `composite = null`, 200 OK with explanatory `detail`s |
| Invoices but no approvals | accuracy N/A; dispute computed; composite = dispute alone |
| No goods receipts | on-time N/A (always N/A this slice regardless) |
| Unknown `vendor_id` | 404 |
| `vendor_id` in another tenant | 404 (tenant-DB scoping makes it not-found, not 403) |

**Compute-on-read, no migration.** The score is a deterministic pure function of
data already in the tenant DB; caching would add a staleness/invalidation
problem (every approval, correction, exception, or GR would have to bump it) for
no correctness gain. The endpoint is on-demand, not a hot list path. If a future
dashboard needs to *sort many vendors by score*, that is the trigger to add a
cached `vendor_scores` column/table (deferred) behind a refresh writer — reusing
the pure scorer unchanged.

## Endpoints

Both are auth + RBAC gated and tenant-scoped (`get_tenant_db` + entity scope).

### `GET /api/enrichment/invoices/{invoice_id}/suggestions`
Roles: `admin`, `ap_manager`, `ap_clerk`, `cfo` (clerks review drafts).

```json
{
  "invoice_id": "…",
  "vendor_id": "…|null",
  "field_suggestions": [
    {"field": "gl_account", "value": "6000", "confidence": "80.0",
     "sample_size": 10, "occurrences": 8,
     "evidence": "8 of 10 prior invoices used 6000", "runner_up": "6100"}
  ],
  "price_variances": [
    {"line_index": 2, "item_key": "code:widget-a", "description": "Widget A",
     "current_unit_price": "12.50", "baseline_unit_price": "10.00",
     "delta": "2.50", "delta_pct": "25.0", "sample_size": 7,
     "direction": "over", "severity": "info"}
  ],
  "generated_at": "2026-06-13T…Z"
}
```

### `GET /api/enrichment/vendors/{vendor_id}/score`
Roles: `admin`, `ap_manager`, `cfo` (managerial — clerk excluded).

```json
{
  "vendor_id": "…", "vendor_name": "Acme Supplies", "composite": "89.9",
  "sub_scores": [
    {"name": "accuracy", "score": "88.0", "sample_size": 25,
     "detail": "22 of 25 approved invoices needed no corrections"},
    {"name": "dispute", "score": "92.5", "sample_size": 40,
     "detail": "3 of 40 invoices raised an exception"},
    {"name": "on_time", "score": null, "sample_size": 0,
     "detail": "On-time delivery requires PO expected dates, not tracked yet"}
  ],
  "computed_at": "2026-06-13T…Z"
}
```

## Config (org settings, all safe defaults — no key required)

Optional overrides under `Organization.settings.enrichment` (merge-over-defaults;
unknown keys dropped, numeric coercion guarded — like `_adaptive_settings`):

| Key | Default |
|---|---|
| `autofill_min_confidence` | `60.0` |
| `autofill_min_sample` | `3` |
| `price_tolerance_pct` | `15.0` |
| `price_escalate_pct` | `30.0` |
| `price_min_history` | `2` |
| `ontime_use_due_date_proxy` | `false` |

## Deferred follow-ups

- **Persist price variance** into `invoice_warnings.refresh_warnings` (+ raise an
  `Exception`) — the pure `detect_price_variance` is built to be reused there.
- **PO expected-date column** to make on-time delivery real (adds a column +
  migration + extraction mapping); on-time stays N/A until then.
- **Cached `vendor_scores` table** for multi-vendor sorting (trigger: a
  sort-by-score dashboard); reuses the pure scorer behind a refresh writer.
- **External enrichment** (D&B / Clearbit) — out of scope, violates local-first.
- **Vendor consolidation / dedup** — basis is `services/vendor_matching.py`.
- **Amount-deviation flagging** already shipped in
  `adaptive_workflows.detect_invoice_anomaly` — intentionally **not** duplicated.
