# PO Matching

## Overview

PO matching compares invoices against purchase orders (and optionally goods receipts) to catch discrepancies before payment. This prevents overpayment, duplicate payment, and fraud.

## Match Types

| Type | What's compared | When to use |
|---|---|---|
| **2-way** | Invoice vs. PO (amount, vendor) | Standard — most invoices |
| **3-way** | Invoice vs. PO vs. Goods Receipt (amount + quantities) | Manufacturing, physical goods |
| **4-way** | Invoice vs. PO vs. Goods Receipt vs. Quality Inspection (adds a pass/fail/partial-acceptance gate) | Regulated / high-spec goods where receipt alone isn't enough (pharma, aerospace, food) |

## How It Works

```
Invoice has po_number?
    |
    ├── No → status: "no_po" (skip matching)
    |
    └── Yes → Find PO by number
              |
              ├── PO not found → status: "no_po", issue flagged
              |
              └── PO found → 2-way match
                    |
                    ├── Amount within tolerance → status: "matched"
                    |
                    └── Amount outside tolerance → status: "mismatch", issue flagged
                          |
                          GR exists for PO?
                          |
                          ├── No → stay as 2-way match
                          |
                          └── Yes → 3-way match
                                ├── Quantities match → status: "matched"
                                └── Partial receipt → status: "partial"
                                      |
                                      QualityInspection exists (by GR, else by PO)?
                                      |
                                      ├── No → if require_inspection: inspection_required,
                                      │        status unchanged, quality_hold (warning) raised
                                      |
                                      └── Yes → 4-way match
                                            ├── result == "pass"    → no status change
                                            ├── result == "partial" → status: "partial"
                                            │                          (accepted_quantity shown)
                                            └── result == "fail"     → status: "mismatch",
                                                                       quality_hold (error) raised
```

The 4-way leg runs **after** the 3-way GR block. It looks up the most recent
`QualityInspection` — preferring the matched GR (`gr_id`), falling back to the
PO (`po_id`). A `fail` blocks the invoice; a `partial` surfaces the accepted
quantity (pay-only-accepted); a `pass` is a clean gate. When
`require_inspection` is on and no inspection exists for a found PO, the match
flags `inspection_required` so the warnings layer can route a `quality_hold`
exception.

## Tolerance

The system allows a configurable variance percentage (default: 5%) between invoice amount and PO total.

| Variance | Result |
|---|---|
| Invoice = $1,500, PO = $1,500 | Matched (0% variance) |
| Invoice = $1,525, PO = $1,500 | Matched (1.7% variance, within 5%) |
| Invoice = $1,600, PO = $1,500 | **Mismatch** (6.7% variance, exceeds 5%) |

## MatchResult

```python
MatchResult:
    match_type: "none" | "2-way" | "3-way" | "4-way"
    status: "no_po" | "matched" | "mismatch" | "partial"
    po_id, po_number, po_total
    gr_id  # if 3-way
    amount_variance: float      # invoice - PO in dollars
    amount_variance_pct: float  # as percentage
    within_tolerance: bool
    inspection_id: str | None              # if 4-way
    inspection_result: "pass" | "fail" | "partial" | None
    inspection_accepted_quantity: float | None  # partial acceptance qty
    inspection_required: bool              # require_inspection on + inspection missing
    issues: list[str]           # human-readable issues
    details: dict               # full match data for audit (has_inspection, inspection_result)
```

`match_invoice_to_po(db, invoice, tolerance_pct=5.0, require_inspection=False)`
takes both knobs; `invoice_warnings._refresh_po_match` resolves them per-invoice
via `services/matching_rules.resolve_match_rule` (see § Per-vendor / per-commodity
rules) rather than reading the org flag directly.

## Integration Points

### After Extraction
PO matching can run after extraction when the invoice has a `po_number`. The match result can be:
- Stored on the invoice (as a warning or in `state_data`)
- Shown in the invoice modal
- Routed to the exception queue if mismatched

### In the Review Step
Reviewers see the match status:
- **Matched** (green) — PO found, amounts within tolerance
- **Mismatch** (red) — PO found but amounts differ
- **Partial** (yellow) — 3-way match, not all goods received
- **No PO** (gray) — no PO number on invoice, or PO not found

### Before Payment
Mismatched invoices can be blocked from the payment queue until the mismatch is resolved (exception cleared).

### Quality-hold exceptions
The 4-way leg routes inspection outcomes to a dedicated `quality_hold`
exception type (created by `invoice_warnings._refresh_po_match`):

| Inspection outcome | Warning severity | `quality_hold` exception |
|---|---|---|
| `fail` | error | created (error) — invoice blocked |
| missing + `require_inspection` on | warning | created (warning) |
| `partial` | info | created (info) — accepted quantity noted |
| `pass` | — | none |

The existing `po_mismatch` handling is unchanged; `quality_hold` is additive.

### Config
Per-org, in `Organization.settings.matching`:

```json
{
  "matching": {
    "require_inspection": false,
    "tolerance_pct": 5.0,
    "vendor_rules":    { "<vendor_id>":        { "require_inspection": true, "tolerance_pct": 2.0 } },
    "commodity_rules": { "<gl_account_code>":  { "require_inspection": true, "tolerance_pct": 1.0 } }
  }
}
```

When `require_inspection` is `true`, an invoice that matches a PO but has **no**
quality inspection on file raises a `quality_hold` warning/exception (the
inspection is mandatory before payment). Default `false` — 4-way only kicks in
when an inspection actually exists.

#### Per-vendor / per-commodity rules

Both knobs — `require_inspection` and the amount `tolerance_pct` — are
configurable per **vendor** and per **commodity type**, not just org-wide.
"Commodity type" is the invoice's header GL account (`invoice.gl_account`); no
new columns. `services/matching_rules.resolve_match_rule(org_settings, vendor_id,
gl_account)` resolves an `EffectiveMatchRule` and `_refresh_po_match` passes the
result into `match_invoice_to_po`.

Precedence is **per-field** (the two knobs resolve independently): for each
field take the first present value walking

```
vendor_rules[str(vendor_id)]  →  commodity_rules[gl_account]  →
matching.<field>  →  hardcoded default (require_inspection=False, tolerance_pct=5.0)
```

So a vendor rule that only sets `require_inspection` still lets `tolerance_pct`
fall through to the commodity / org / default layers. The resolver is pure (no
DB / I/O) and never raises — malformed config (non-dict rules, missing keys,
`None` vendor/GL, non-numeric tolerance) silently falls to the next layer. The
returned `source` ("vendor" | "commodity" | "org" | "default") records where
`require_inspection` resolved from, for logging.

### QMS integration (inspection sync)

Quality-inspection rows can be pulled from an external QMS / LIMS rather than
only entered by hand. Same pluggable-adapter shape as the other provider
families (`financing_adapters`, `fx_adapters`, …):

- **Adapters** (`services/qms_adapters/`): `mock` (deterministic pass/fail/partial
  fixtures, no network/credential — the local-first default) and `generic` (an
  httpx skeleton that **fails closed** without a per-org `base_url` + `api_key`;
  no hardcoded secret). Registry via `@register_qms_adapter`; selected per-org via
  `Organization.settings.qms.provider`, falling back to `AP_QMS_PROVIDER`
  (default `mock`). Contract: `async fetch_inspections(*, since=None) ->
  list[QMSInspectionRecord]` + `async test_connection() -> bool`.
- **Sync** (`services/qms_sync.py`): `sync_tenant_inspections` fetches records,
  resolves each record's `po_number` / `gr_number` to local `PurchaseOrder` /
  `GoodsReceipt` ids, then **upserts** a `QualityInspection` idempotently keyed on
  `(organization_id, inspection_number)` (re-run updates in place, never
  duplicates). Each landed record writes an append-only `quality_inspection.synced`
  audit row (PII-free: inspection number + resolution outcome only). After the
  upsert it best-effort re-runs `invoice_warnings.refresh_warnings` (inside a
  SAVEPOINT) for invoices referencing the affected POs so a fresh quality verdict
  re-gates the 4-way match — never fails the sync.
- **Sweep** (`run_qms_sync_loop`): a long-lived asyncio task (mirrors
  `contract_renewal`) that sweeps every tenant DB on `AP_QMS_SYNC_INTERVAL_SECONDS`.
  Disabled by default (`AP_QMS_SYNC_ENABLED=false`); orgs without a `settings.qms`
  block are skipped while the platform provider is `mock`.
- **Manual trigger**: `POST /api/inspections/sync` (admin / ap_manager) runs one
  sync for the current tenant from `Organization.settings.qms`, returning
  `{fetched, created, updated}`.

## Data Models

The procurement models already exist:

| Table | Purpose |
|---|---|
| `purchase_orders` | PO header (po_number, vendor_id, total, status) |
| `po_line_items` | PO lines (description, quantity, unit_price, total) |
| `goods_receipts` | GR header (gr_number, po_id, received_date, status) |
| `gr_line_items` | GR lines (description, quantity_received) |
| `quality_inspections` | Inspection header (inspection_number, po_id, gr_id, result, accepted/rejected_quantity, deviation_notes) — the 4-way leg |

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/invoices/{id}/match` | Run PO matching for an invoice (planned) |
| `GET` | `/api/invoices/{id}/match-result` | Get the match result (planned) |

> The matcher has no dedicated HTTP entry point — it runs inside
> `invoice_warnings.refresh_warnings`, which fires on every invoice mutation
> (`PATCH /api/invoices/{id}`), persisting the result on `Invoice.po_match`.
> `POST /api/invoices` does **not** run it, and the `_refresh_po_match` guard
> skips draft `new` invoices, so the computed `po_match` first appears after the
> invoice leaves `new` and is next PATCHed.

## End-to-end coverage

`frontend/tests-e2e/matching/` exercises the matcher through the real
PATCH→`refresh_warnings`→`match_invoice_to_po` path (PO/GR rows seeded via
`tenantPsql`, inspections via `POST /api/inspections`, invoice via the API):

- `two-three-way.spec.ts` — 2-way tolerance band (within / boundary `<=5%` /
  outside → `mismatch` + `po_mismatch` exception by severity), `no_po`,
  fractional-cent variance precision, 3-way full/partial/amount-mismatch
  outcomes, `po_match` clears when `po_number` is removed, recompute idempotence.
- `four-way-inspection.spec.ts` — Quality-Inspection gate (`pass`/`fail`/`partial`
  → status + `quality_hold` severity), late inspection re-gating on recompute,
  org-wide `require_inspection` (missing → `quality_hold` warning), commodity-GL
  tolerance override.
- `rules-and-isolation.spec.ts` — `matching_rules` per-field precedence
  (vendor > commodity > org > default; malformed rule fails soft) asserted via
  `po_match.details.tolerance_pct`, and tenant isolation (a PO is invisible to a
  different tenant).
- `inspections-api.spec.ts` — `/api/inspections` create/list/detail round-trip,
  result-enum + bad-uuid 400s, 404, and the create RBAC gate (clerk denied).

`goods-receipts/three-way-feed.spec.ts` proves a GR actually changes the match
outcome (presence → 3-way; short receipt → `partial`).

## Implementation Status

| Feature | Status |
|---|---|
| PO matching service (2-way, 3-way, 4-way) | Done |
| Quality inspection model (`quality_inspections`, alembic 0033) | Done |
| 4-way match (Invoice vs PO vs GR vs Quality Inspection) | Done |
| `quality_hold` exception routing (fail → error, missing → warning, partial → info) | Done |
| Partial acceptance (`accepted_quantity` surfaced in match + modal) | Done |
| Configurable `require_inspection` per org (`Organization.settings.matching.require_inspection`) | Done |
| Inspections API (`/api/inspections` list/create/detail) | Done |
| Inspection display in invoice modal (Quality Inspection sub-panel) | Done |
| QMS integration (`qms_adapters` mock + generic skeleton, `qms_sync` sweep, `POST /api/inspections/sync`) | Done |
| Per-vendor / per-commodity match rules (`services/matching_rules.py`, vendor/GL `require_inspection` + `tolerance_pct` overrides) | Done |
| Tolerance configuration | Done (5% default) |
| Vendor-aware matching (match PO by vendor_id) | Done |
| Goods receipt quantity comparison | Done |
| Procurement models (PO, GR) | Done (existed) |
| Wired into extraction + invoice-mutation pipeline (`services.invoice_warnings.refresh_warnings`) | Done |
| Persisted on `invoice.po_match` (JSONB, alembic 0006) | Done |
| Match result display in invoice modal (PO Match panel with status badge, variance, issues) | Done |
| Exception routing for mismatches (`po_mismatch` exceptions auto-created by severity: error / warning / info) | Done |
| PO management UI (list + detail page) | Planned |
| PO sync from ERP (real adapter `list_pos()` — currently mock data) | Planned |
| Configurable tolerance per org (`Organization.settings.po_matching.tolerance_pct`) | Planned |
