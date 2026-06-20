# Intelligent Data Enrichment from Supplier History

Advisory enrichment derived **deterministically** from each tenant's *own*
historical invoice data. No external calls, no cloud key — runs on a laptop
with `pnpm dev` and the mock adapters (local-first invariant). Four surfaces,
three read-only endpoints, all suggestion-only / compute-on-read.

A fifth surface — **external enrichment** (firmographics from D&B / Clearbit) —
is bolted on via a pluggable adapter family with a deterministic `mock` default,
so it too runs with no cloud account; the real providers fail closed without a
key. See [§ External enrichment (D&B / Clearbit)](#external-enrichment-db--clearbit) below.

| Surface | What it does | Persists? |
|---|---|---|
| **Auto-fill** | Suggests the dominant historical `gl_account` / `cost_center` / `payment_terms` for a draft invoice's vendor | No — advisory |
| **Price variance** | Flags draft line items whose unit price deviates from the vendor's per-item historical median | Yes — also persisted as a warning + `price_variance` exception on every invoice mutation (returned inline too) |
| **Vendor scoring** | Accuracy + dispute (+ optional on-time) sub-scores → renormalized composite | No — compute-on-read |
| **Vendor consolidation** | Clusters likely-duplicate / similar vendors so the master list can be deduped | No — advisory, never merges |

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

**Persisted as a warning + exception (the follow-up, now shipped).**
`invoice_warnings.refresh_warnings` — the single write chokepoint that runs on
every invoice mutation and after every extraction — calls
`_refresh_price_variance`, which reuses the **same pure `detect_price_variance`**
math (no re-implementation). For an extracted invoice (status `!= new`) with a
`vendor_id`, it:

- pulls this vendor's approved-or-beyond historical line items (same set + same
  `(item, currency)` keying as the advisory endpoint, bounded by
  `PRICE_HISTORY_LIMIT`) and the draft's own line items;
- appends one `price_variance` warning per flagged line to `Invoice.warnings`
  (severity `warning`/`info`, same thresholds as the inline surface); and
- raises **one** de-duped `price_variance` `Exception` covering all flagged
  lines (severity escalates to `warning` if any line cleared the escalate
  threshold). Dedup is via `_ensure_exception` — re-running on the same invoice
  never piles up duplicate open exceptions, exactly like `fraud_stat_anomaly`.

Gated by the `price_variance_enabled` fraud rule (default `true` — set
`settings.fraud_rules.price_variance_enabled: false` to opt out, like the other
fraud rules). Tolerance / escalate / min-history come from the **same**
`settings.enrichment` block the advisory endpoint uses, so the persisted warning
and the inline advisory always agree. Best-effort: a failure in the check is
swallowed (PII-free log) and never blocks saving the invoice. The message
carries only the item label + prices + percent — no vendor PII. Covered by
`backend/tests/test_price_variance_warning.py`. The inline read-only endpoint is
unchanged.

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

## Vendor consolidation (duplicate / similar vendor clusters)

`find_consolidation_clusters(vendors) -> (list[VendorCluster], truncated)` in
`app/services/vendor_consolidation.py`. Pure, sync, DB-free — the API layer
(`app/api/enrichment.py`) pulls a lightweight vendor projection + per-vendor
invoice counts and hands them in as `VendorRecord`s.

Identifies CLUSTERS of likely-duplicate vendors so a steward can dedupe the
master list. **Advisory only** — it suggests a canonical/primary candidate per
cluster but NEVER merges or mutates anything (that auto-merge is a deferred
follow-up below).

### Evidence + clustering

- Reuses the fuzzy primitives from **`services/vendor_matching.py`** (`_normalize`
  — drops `Inc`/`LLC`/`Ltd`/… suffixes + punctuation; `_similarity` — Jaccard
  token overlap, 0..1), not a reinvented matcher. So `"Acme Supplies Inc."` and
  `"Acme Supplies, LLC"` normalize to the same token bag and cluster.
- A pair clusters on **any** of (strongest first): same normalized `tax_id`
  (score `1.0`, definitive), same normalized `code` (score `0.95`), or fuzzy
  name similarity `>= NAME_SIMILARITY_THRESHOLD` (default `0.6`; score = the
  similarity). A tax-id / code match clusters **regardless of name** — a typo'd
  name is exactly the duplicate we want to catch.
- Clustering is **transitive** (union-find): A~B by tax id and B~C by code put
  all three in one cluster even if A and C share no direct evidence — they're
  the same vendor seen three ways.
- Only `active` / `unverified` vendors are scanned — consolidating the *live*
  master list is the point; `inactive` / `rejected` rows are already handled.

### Canonical pick (deterministic)

Per cluster: **most invoice volume** wins; tie → **oldest** (lowest `age_rank`,
the caller's `created_at asc` row index); final tie → lowest id (stable). The
canonical member sorts first in `members`; the rest follow by invoice volume
desc. Two runs over the same data return byte-identical output.

### Performance bound (no silent O(n²))

Vendors are first partitioned into **blocks** by a cheap key — exact normalized
`tax_id`, exact normalized `code`, and the normalized name's **first token** —
and the quadratic fuzzy comparison runs only *within* a block. Two vendors are
compared only if they already share one of those keys, collapsing the worst case
from N² to Σ(block size)². A hard `MAX_VENDORS` (5000) backstop skips clustering
entirely above the cap (`truncated=true`, no clusters); `MAX_CLUSTERS` (200)
caps the emitted clusters (strongest first, tail dropped → `truncated=true`).

### PII

A vendor's full `tax_id` never leaves the service or enters a response / log.
The clustering hashes the **normalized** tax id internally to bucket and to
decide "same tax id", but only a masked `***<last4>` (`mask_tax_id`) is emitted
on each member.

## Endpoints

All three are auth + RBAC gated and tenant-scoped (`get_tenant_db` + entity scope).

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

### `GET /api/enrichment/vendors/consolidation-suggestions`
Roles: `admin`, `ap_manager`, `cfo` (managerial data-stewardship view — clerk
excluded). No path params — scans the whole (entity-scoped) vendor book.

```json
{
  "clusters": [
    {
      "cluster_id": 1,
      "canonical_vendor_id": "…",
      "score": "1.00",
      "reasons": ["names 0.93 similar", "same tax id"],
      "members": [
        {"vendor_id": "…", "name": "Acme Supplies Inc.", "code": "ACME",
         "tax_id_masked": "***6789", "status": "active",
         "invoice_count": 12, "is_canonical": true},
        {"vendor_id": "…", "name": "Acme Supplies, LLC", "code": null,
         "tax_id_masked": "***6789", "status": "unverified",
         "invoice_count": 3, "is_canonical": false}
      ]
    }
  ],
  "vendor_count": 140,
  "cluster_count": 1,
  "truncated": false,
  "generated_at": "2026-06-19T…Z"
}
```

## External enrichment (D&B / Clearbit)

The four surfaces above are derived from the tenant's own history. **External
enrichment** instead looks a vendor up in a third-party firmographics provider
(Dun & Bradstreet, Clearbit) and returns a normalised record — legal name,
registered address, country, industry + SIC/NAICS, employee count, annual
revenue, website, DUNS, founding year — so an AP steward can review and
selectively apply it. It is built as a **pluggable adapter family**
(`services/enrichment_adapters/`) following the project's local-first adapter
pattern (the same shape as `sanctions_adapters` / `fx_adapters` /
`billing_adapters`).

### Adapters

| Provider | Network / key | Behaviour |
|---|---|---|
| `mock` (default) | none | Deterministic synthetic firmographics from a hash of the vendor name (legal name carries a `(MOCK)` marker so it can't be mistaken for real data). A built-in no-match fixture set + a `mock_no_match` override exercise the "couldn't enrich" branch. Local-first — `pnpm dev` + the whole test suite run against it with no cloud account. |
| `dun_bradstreet` | `httpx` to D&B Direct+ | Skeleton — `cleanseMatch` (resolve to a DUNS) → `data/duns/{duns}` firmographics block, request/response shapes match the published API. **Fails closed** (`EnrichmentNotConfigured`) without a per-org `api_key`; no hardcoded fallback secret. |
| `clearbit` | `httpx` to Clearbit Company API | Skeleton — domain-keyed `companies/find`. A vendor with no email-derived domain is a clean no-match (not an error). **Fails closed** without a per-org `api_key`. |

Registry via the `@register_enrichment_adapter` decorator;
`get_enrichment_adapter(config)` resolves per-org
`Organization.settings.enrichment.provider` → the `AP_VENDOR_ENRICHMENT_PROVIDER`
env default (`mock`). An unknown provider name falls back to `mock` (a typo can't
break enrichment), but the real providers still fail closed on a missing key.

To add a provider: copy `mock_adapter.py`, implement `enrich_vendor` +
`test_connection`, register with the decorator.

### `POST /api/enrichment/vendors/{vendor_id}/enrich`

Roles: `admin`, `ap_manager`, `cfo` (managerial data-stewardship action that may
consume a metered external API — clerk excluded, like the score + consolidation
surfaces). Tenant-scoped via `get_tenant_db` + `get_tenant`.

**Advisory / suggestion-only — never overwrites.** The response carries the
looked-up firmographics plus a per-field `suggestions` diff (where the provider's
value differs from what we hold today); the enrichment path **never writes back
onto the `Vendor` row**. Applying a suggestion is a separate, explicit step (a
deferred follow-up — see below), mirroring the consolidation surface's
"suggest a canonical, never merge" stance.

**PII.** A vendor's raw `tax_id` is passed to a provider as a match key (an input
only) but is **never echoed back** — only a masked `***<last4>` (`mask_tax_id`,
reused from `vendor_consolidation`) ever appears in the response, and no PII
enters a log line. A keyless real provider returns a PII-free 422 (the message
names only the missing `api_key`, never a secret).

```json
{
  "vendor_id": "…",
  "vendor_name": "Acme Supplies",
  "firmographics": {
    "provider": "mock", "matched": true,
    "legal_name": "Acme Supplies (MOCK)", "address": "1 Mock Plaza, Suite 100",
    "country": "US", "industry": "Commercial Printing", "sic_code": "2752",
    "naics_code": "323111", "employee_count": 1828, "annual_revenue": "457000000",
    "website": "https://acmesupplies.example", "duns_number": "388666768",
    "year_founded": 1982, "tax_id_masked": "***6789", "confidence": 88,
    "extra": {"source": "mock", "deterministic": true}
  },
  "suggestions": [
    {"field": "address", "current_value": null, "suggested_value": "1 Mock Plaza, Suite 100"}
  ],
  "generated_at": "2026-06-20T…Z"
}
```

`annual_revenue` is a **string** on the wire (never a float — money invariant).

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

- ~~**Persist price variance** into `invoice_warnings.refresh_warnings` (+ raise an
  `Exception`)~~ — **DONE.** `_refresh_price_variance` reuses the pure
  `detect_price_variance` at the `refresh_warnings` chokepoint; see the Price
  variance section above.
- **PO expected-date column** to make on-time delivery real (adds a column +
  migration + extraction mapping); on-time stays N/A until then.
- **Cached `vendor_scores` table** for multi-vendor sorting (trigger: a
  sort-by-score dashboard); reuses the pure scorer behind a refresh writer.
- ~~**External enrichment** (D&B / Clearbit)~~ — **DONE.** Built as a pluggable
  adapter family with a deterministic `mock` default (local-first preserved) +
  fail-closed `dun_bradstreet` / `clearbit` skeletons. See
  [§ External enrichment](#external-enrichment-db--clearbit). Remaining:
  - **Apply a suggestion** — the enrich endpoint is advisory (returns a
    firmographics diff, never mutates the `Vendor` row). An explicit "apply this
    suggested address / website" write path (audited, idempotent) is deferred.
    Trigger: an "Apply" action in the vendor-detail UI. A `Vendor.website` column
    would also be needed before the website suggestion can land (the model has no
    website column today — the suggestion is surfaced but not yet applicable).
  - **Live D&B / Clearbit calls** — the real adapters are working skeletons
    (request/response shapes match the published APIs) but need a live key wired
    via sops per-org before they call out; until then they fail closed.
- **Auto-merge of a consolidation cluster** — the consolidation endpoint is
  advisory (suggests a canonical candidate, never mutates). An actual merge
  (re-point invoices/POs/payments to the canonical vendor, retire the
  duplicates, write an audit row, idempotent) is a heavier *write* path —
  deferred. Trigger: a "Merge into canonical" action in the UI.
- **Amount-deviation flagging** already shipped in
  `adaptive_workflows.detect_invoice_anomaly` — intentionally **not** duplicated.
