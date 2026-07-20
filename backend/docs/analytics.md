# Analytics & Reporting

Operational + CFO-grade analytics for AP. The dashboard at
`/api/dashboard` is the AP-clerk surface (pipeline, aging, recent
payments, processing-time metrics). The CFO surface at
`/api/analytics/cfo` adds finance-leader metrics (DPO, cash
conversion cycle, accruals, supplier concentration, rebate yield,
forecast variance). Both are computed by pure functions in
`services/analytics.py`; SQL lives in the API layer.

## Layers

| Layer | File | Purpose |
|---|---|---|
| Compute | `app/services/analytics.py` | Pure functions — DPO, processing time, supplier concentration, ... |
| Operational dashboard | `app/api/dashboard.py` | Pipeline / aging / processing time / approval bottleneck / discount capture (AP clerk + manager + CFO) |
| CFO dashboard | `app/api/analytics.py::get_cfo_analytics` | DPO + trend, CCC, accruals, working-capital impact, supplier concentration, fraud-rate trend, rebate yield (admin + CFO only) |
| Drill-through | `/api/analytics/drill/*` | Per-metric "show me the rows" endpoints (spend_concentration, dpo) |
| Cash-flow forecasting | `/api/analytics/{cashflow_forecast,cashflow_whatif,cash_position}` | Predictive AP outflow buckets, payment-timing what-if, running cash position with bank-balance auto-sync (admin + CFO only). Web dashboard at `/cfo`. |
| Cash-position thresholds | `/api/analytics/cash-position-settings` (GET/PUT) | Persisted per-org low-balance alert threshold on `settings.cashflow` (no migration). |
| CSV export | `app/services/report_export.py` + `/api/analytics/export/{report}` | invoice_register, vendor_spend, payment_register, aging_snapshot, cashflow_forecast, expense_register |
| Scheduled delivery | `app/services/scheduled_reports.py` + migration 0020 | Per-tenant cron-like subscriptions; daily / weekly / monthly cadence; email via existing adapter |

## Operational metrics (dashboard)

Existing fields stay: `pipeline`, `vendor_spend`, `aging`,
`monthly_trend`, `upcoming_payments`, `touchless_rate`, `total_*`.

- `aging` — open-invoice exposure bucketed by **days past the due date**:
  `current` (not yet due), `days_30` (1-30), `days_60` (31-60), `days_90`
  (61-90), `days_90_plus` (90+). The same five buckets back the
  `aging_snapshot` CSV export.
- `touchless_rate` — straight-through-processing rate: invoices that cleared
  review without manual rework (reached `approved` or beyond) over every
  invoice that has finished review (those same states **plus** `rejected`).
  The numerator is a strict subset of the denominator, so the value is always
  in `[0, 100]` — it can never go negative.

New keys added in a prior iteration:

- `processing_time` — avg / median / p95 days for (upload→approval)
  and (upload→paid). Sample size below 5 collapses to zeros
  (`count_*_leg` still reported) so a tiny tenant doesn't see
  noisy numbers driven by one outlier.
- `approval_bottleneck` — per-approver pending counts, oldest
  pending age, average age. Top 10 returned, sorted by
  pending_count descending. Unassigned steps roll under the
  synthetic `unassigned` approver_id — a non-zero bucket there
  is its own routing-broken signal.
- `discount_capture` — eligible / captured / missed counts and
  amounts, with capture_rate_pct. Eligibility comes from
  `PaymentSchedule.discount_percent`; capture is determined by
  whether the matching completed Payment's `completed_at` ≤
  `discount_date`.

## CFO metrics (`GET /api/analytics/cfo`)

Query params: `period_days` (default 365, range 30–730).

Response:
- `total_spend`, `accounts_payable_balance`, `avg_daily_outflow`
- `dpo_current` + `dpo_trend` (last 6 months)
- `cash_conversion_cycle` (NULL when DSO/DIO not available — the
  AP-only product can't compute it)
- `accruals.{open_po_amount, received_amount, unposted_invoice_amount, total_accrual}`
  (`received_amount` values goods physically received but not yet
  invoiced — the GR/IR accrual leg. The 3-way match is fanned out per
  PO: each receipted PO contributes `po_total × min(1, gr_qty/po_qty)`,
  the same received-fraction the PO matcher computes. POs with no
  quantified lines but a booked receipt count as fully received;
  receipts with no PO link can't be priced and are excluded. Pure math
  in `analytics.value_received_goods`; SQL fan-out in
  `api/analytics._received_amount`.)
- `working_capital_impact_5_days` — `avg_daily_outflow × 5`
- `supplier_concentration.{top_10_share_pct, top_50_share_pct, largest_vendor, largest_vendor_share_pct, flagged}` — `flagged=true` iff the largest vendor exceeds 25% (configurable)
- `fraud_rate_trend` — exceptions / invoices × 100 per month
- `rebate_yield.{yield_pct, annualised_rebates, ...}`

Drill-through:
- `GET /api/analytics/drill/spend_concentration?period_days=N&limit=N`
- `GET /api/analytics/drill/dpo?months=N`
- `POST /api/analytics/forecast_variance` — body `{"months":
  [{"month": "YYYY-MM", "forecast": "100000"}, ...]}`. Server
  fills in `actual` from completed payments and returns
  `{rows: [{forecast, actual, variance, variance_pct}, ...]}`.
  Forecasts are NOT persisted — the CFO pastes from their FP&A
  tool.

## Consolidated reporting across entities (`GET /api/analytics/by-entity`)

Read-only, admin + CFO only. A side-by-side **per-entity AP rollup PLUS a
consolidated total** — the multi-entity "consolidated reporting across
entities" view. Every other endpoint in `analytics.py` reports either ONE
entity (the `X-Entity-ID` selection) or the fully-merged consolidated total;
this one returns **all active entities at once** so subsidiaries can be
compared side by side.

Unlike its neighbours it **intentionally ignores `X-Entity-ID`** — it doesn't
depend on `get_entity_id`. Instead it lists active entities directly
(`is_default` first, then by name — matching the entity switcher's order) and
calls the shared `_entity_metrics(entity_id=...)` helper once per entity, then
once more with `entity_id=None` for the `consolidated` block. Because every row
and the consolidated block run the same entity-scoped query shapes used by
`/analytics/cfo` (total spend, open-payables balance, invoice count,
open-exception count, open-PO accrual via `_open_po_sum_query`), the
consolidated block is a true sum-across-entities cross-check.

Query params: `period_days` (default 365, range 30–730).

Response:
- `period_days`, `period_start`
- `entities[]` — one row per active entity:
  `{entity_id, entity_name, entity_slug, currency, is_default, total_spend,
  outstanding_amount, invoice_count, open_exceptions, open_po_amount}`
- `consolidated` — the same metric block computed with `entity_id=None`
  (equals the sum across `entities[]`)

Money fields (`total_spend`, `outstanding_amount`, `open_po_amount`) are
**string-Decimal** (never floats). A single-entity tenant still returns a
coherent one-row breakdown whose row equals the consolidated block. The web
surface is the `By entity` table on `/cfo`
(`frontend/src/lib/components/analytics/ByEntityBreakdown.svelte`), which
self-hides for single-entity tenants (mirrors the entity switcher).

## Predictive cash-flow forecasting (`/api/analytics/{cashflow_forecast,cashflow_whatif,cash_position}`)

Read-only, admin + CFO only. All three resolve the tenant DB through
`get_tenant_db`. Source rows: open invoices `LEFT JOIN payment_schedules`
on `invoice_id`, timed on the schedule's `due_date` (falling back to
`Invoice.due_date`), bounded to `[today, today + horizon_days]`. Money is
`Decimal` end-to-end (floated only at the JSON boundary). No writes, no
audit rows, no new migration — pure aggregate math, like
`forecast_variance`.

**Committed vs pending status sets** (the rest — `rejected`, `paid`,
`done`, `failed` — are excluded):

- **committed** (firm AP commitment): `approved`, `sending_to_erp`,
  `sent_to_erp`, `posted_in_erp`, `payment_scheduled`.
- **pending** (in-flight pipeline, lower-certainty projection): `new`,
  `pending`, `ready_for_review`. Drop with `include_pending=false`.

`GET /cashflow_forecast?granularity=day|week|month&horizon_days=N&include_pending=bool`
— per-period `{scheduled_amount, committed_amount, pending_amount,
discount_eligible_amount, count}` plus a `totals` rollup. Weeks are
Monday-anchored. Default `granularity=week`, `horizon_days=90`.

`GET /cashflow_whatif?granularity=…&horizon_days=N&grace_days=N` —
compares three payment-timing scenarios, each with `total_outflow`,
`total_discount_captured`, amount-weighted `weighted_avg_pay_date_days`,
and bucketed `periods`:

- `on_time` — pay on `due_date`, full amount.
- `early` — pay on `discount_date` when present, net of
  `discount_percent` (the captured discount is reported separately); rows
  without a discount fall back to the due date at full amount.
- `late` — pay `due_date + grace_days`, full amount, discount forfeited.

`GET /cash_position?granularity=…&horizon_days=N&opening_balance=STR&min_balance_threshold=STR&seed_balance=bool`
— running balance carried forward per period
(`closing = opening − outflow`; receivables/inflows aren't modelled in an
AP-only product). Periods that close below the effective threshold are flagged
(`below_threshold`) and collected in `breaches[]` with the `shortfall`. Money
params are parsed as `Decimal` strings (never floats); garbage → 400.

**Opening balance — resolution order** (first hit wins; the chosen source is
echoed as `opening_balance_source`):

1. `opening_balance` query param — explicit bring-your-own override → `"query"`.
2. **Bank-balance auto-sync** — pulled from the org's configured
   payment/banking provider via the optional `PaymentAdapter.get_balance`
   capability → `"provider"` (with `opening_balance_currency`). Only attempted
   when the org has a `settings.payments` provider configured and
   `seed_balance` is true (default). **Best-effort** — an adapter that doesn't
   implement the capability, or a fetch that fails, silently falls through to
   the next source; the dashboard never 500s on a bank-link outage, and nothing
   logs the balance figure or any account number. The local-first `mock` adapter
   returns a deterministic figure (250000.00) so `pnpm dev` needs no real bank
   credential. Pass `seed_balance=false` to skip the provider call.
3. Persisted `Organization.settings.cashflow.opening_balance` → `"settings"`.
4. `0` with `opening_balance_source: "none"` so the UI prompts for one.

**Threshold** — the `min_balance_threshold` query param when supplied, else the
org's persisted `settings.cashflow.min_balance_threshold` (see below).

`get_balance` is an OPTIONAL adapter capability (`services/payment_adapters/base.py`
→ `BalanceResult`): the base-class default reports `available=False`, so existing
adapters that don't implement it are unaffected. The best-effort fetch +
fallback chain live in `services/cashflow.fetch_provider_balance` (returns `None`
on unsupported / failure — never raises).

### Persisted cash-position thresholds (`/api/analytics/cash-position-settings`)

Per-org alert thresholds persisted on `Organization.settings.cashflow` (JSON —
**no migration**), so the CFO sets them once instead of passing
`min_balance_threshold` on every request. CFO + admin (same surface as the
cash-position view); the PUT is audited (`organization.cash_thresholds_updated`,
PII-free).

- `GET /cash-position-settings` → `{ "min_balance_threshold": "STR" | null }`.
- `PUT /cash-position-settings` body `{ "min_balance_threshold": "STR" | null }`
  — money is an exact `Decimal` serialised as a JSON string (never a float);
  `null` clears it; a negative value → 422. The write preserves any other
  `cashflow` keys (e.g. a manually set `opening_balance`).

The resolver/store helpers (`services/cashflow.resolve_cash_thresholds` /
`store_cash_thresholds`) tolerate a missing/malformed `cashflow` block by
returning "no threshold" rather than raising.

## CSV export

`GET /api/analytics/export/{report}?period_days=N` returns
`text/csv` with a Content-Disposition header. Supported reports:

| `{report}` | Columns |
|---|---|
| `invoice_register` | invoice_id, invoice_number, vendor_name, amount, currency, status, invoice_date, due_date, created_at, po_number |
| `vendor_spend` | vendor_name, invoice_count, total_amount |
| `payment_register` | payment_id, invoice_id, invoice_number, vendor_name, amount, currency, method, status, provider, reference, submitted_at, completed_at |
| `aging_snapshot` | as_of_date, current, days_30, days_60, days_90, days_90_plus, total |
| `cashflow_forecast` | period, period_start, period_end, scheduled_amount, committed_amount, pending_amount, discount_eligible_amount, count |
| `expense_register` | date, merchant, category, amount, currency, gl_code, payment_method, status, report_number |

`cashflow_forecast` is forward-looking — it takes `granularity` +
`horizon_days` (not `period_days`) and runs the same forecast query as the
JSON endpoint.

Column order is pinned by `tests/test_report_export.py` — finance
imports rely on column position; a reorder breaks downstream
pipelines.

### Formula-injection guard (CWE-1236)

Every CSV export surface runs string cells through the shared
`report_export.csv_safe_cell` helper: a string whose first character is `=`,
`+`, `-`, `@`, tab or CR is prefixed with a single quote so Excel /
LibreOffice treat it as literal text instead of executing it as a formula (a
vendor name comes from AI extraction, i.e. from the attacker's own invoice
PDF). Non-string cells and signed numeric strings (`-12.34` —
Decimal-formatted amounts) pass through untouched, so money columns are
byte-identical. Applied in the `report_export` exporters (analytics export,
scheduled reports, expense export), the audit export
(`/api/audit/export?format=csv`), the report builder
(`/api/reports/{id}/export?format=csv`), the invoice bulk export
(`POST /api/invoices/bulk/export`), and the single-invoice workflow export
(`GET /api/workflow/{id}/export?format=csv`). The **Positive Pay** files are
deliberately excluded — fixed-format bank-machine uploads where a `'` prefix
would break the bank's payee exact-match (see `docs/positive-pay.md`).
Pinned by `tests/test_csv_injection.py` + `tests/test_bulk_export_csv_injection.py`.

**Dispatch is exhaustive by construction**: `export_report`'s branch-per-report
`if/elif` ends in an `else` that raises rather than falling through to any one
report's query — every key in `EXPORTERS` must have its own branch above it.
Before this was fixed (issue #120), `expense_register` had no branch and
silently fell into the `aging_snapshot` query, feeding its bucket dict into
`export_expense_register` and returning a 200 with a corrupted CSV (blank real
columns, bucket-key characters landing in `report_number`/`gl_code`). The
scheduled-report materializer (`services/scheduled_reports._materialise_rows`)
had the identical bug and mirrors the same exhaustive-dispatch-with-raise
shape.

## Scheduled report delivery

Migration 0020 adds `scheduled_reports`. Rows:

- `name` — display label
- `report_type` — one of the keys in `report_export.EXPORTERS`
- `cadence` — `daily` | `weekly` | `monthly`
- `recipients` — JSONB list of email addresses
- `period_days` — window the report covers
- `next_run_at` — when the runner picks it up
- `last_run_at` / `last_run_status` / `last_run_error`
- `enabled`

The runner (`services/scheduled_reports.run_scheduled_reports_loop`
— wired into `main.lifespan`, gated by `AP_SCHEDULED_REPORTS_ENABLED`,
disabled by default so local dev / tests never email) ticks every
`AP_SCHEDULED_REPORTS_TICK_SECONDS`. Each tick `run_scheduled_reports_once`
fans out across every tenant DB (`_sweep_tenant`), calls `list_due_schedules`,
then `execute_schedule` per due row — one tenant's failure never halts the
sweep. Success bumps `next_run_at` forward by the cadence and clears the error;
failure persists a `[retry N]` marker and bumps the count. After
five consecutive failures the row is auto-disabled so the queue
doesn't loop forever — an operator re-enables from the admin UI
after fixing the underlying issue.

Email-adapter exceptions never leak provider-side details into
`last_run_error` (invariant #7); only the exception class name
is stored.

## Known gaps

- **Cash conversion cycle**: AP-only; DSO + DIO need data we
  don't carry. Returns NULL; the UI renders "needs receivables
  data" rather than a misleading 0.
- **PDF export**: only CSV today. PDF needs reportlab/weasyprint
  + templates; a separate piece.

## Tests

| File | Coverage |
|---|---|
| `tests/test_analytics.py` | 27 cases — every compute_* function: DPO formula, CCC None-on-missing-legs, working-capital monotonicity, supplier concentration flag threshold, fraud-rate zero-invoice safety, rebate annualisation, forecast-variance sign convention, processing-time min-sample collapse, approval-bottleneck rollup + unassigned bucket, discount-capture empty safety |
| `tests/test_report_export.py` | 11 cases — registry pins all four reports; per-report header column-order pinned; enum-status reads `.value`; missing fields emit empty (not "None"); orphan payment-with-null-invoice still emitted |
| `tests/test_scheduled_reports.py` | 11 cases — cadence delta math; unknown-cadence fallback; happy-path generates → emails every recipient → updates next_run_at; generator-error / empty-recipients / email-adapter-error all persist a failure marker without raising; PII guardrail (no SMTP transport details in `last_run_error`); five-consecutive-failures disables the row, first failure leaves enabled alone |
| `tests/test_dashboard_aggregations.py` | Existing — extended through the new branches via the try/except absorption pattern |
| `tests/test_cashflow_balance.py` | Unit — `get_balance` capability (base-class default unsupported; mock deterministic + config override + simulated-unsupported); `fetch_provider_balance` best-effort (mock balance, None on unsupported, swallows adapter error); persisted-threshold resolve/store round-trip + garbage tolerance + key preservation/clear |
| `tests/test_cashflow_forecast_api.py` (cash-position additions) | API — auto-seed opening balance from the mock provider (`source: provider`); `seed_balance=false` skips it; query param beats provider; provider-unsupported falls back to `settings`; persisted threshold applied without a query override; `cash-position-settings` GET/PUT round-trip; negative → 422; RBAC (ap_clerk 403, admin/cfo 200) |
| `tests/test_analytics_by_entity.py` | `/by-entity` — per-entity spend/invoice-count scoping for two entities; `consolidated` equals the cross-entity sum; open-exceptions scope per entity; single-entity tenant returns a coherent one-row breakdown; RBAC (ap_clerk/ap_manager 403, cfo 200); the endpoint ignores `X-Entity-ID` |
