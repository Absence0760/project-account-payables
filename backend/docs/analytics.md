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
| CSV export | `app/services/report_export.py` + `/api/analytics/export/{report}` | invoice_register, vendor_spend, payment_register, aging_snapshot |
| Scheduled delivery | `app/services/scheduled_reports.py` + migration 0020 | Per-tenant cron-like subscriptions; daily / weekly / monthly cadence; email via existing adapter |

## Operational metrics (dashboard)

Existing fields stay: `pipeline`, `vendor_spend`, `aging`,
`monthly_trend`, `upcoming_payments`, `touchless_rate`, `total_*`.
New keys added in this iteration:

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
  (received_amount is approximated 0 today — see "Known gaps")
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

## CSV export

`GET /api/analytics/export/{report}?period_days=N` returns
`text/csv` with a Content-Disposition header. Supported reports:

| `{report}` | Columns |
|---|---|
| `invoice_register` | invoice_id, invoice_number, vendor_name, amount, currency, status, invoice_date, due_date, created_at, po_number |
| `vendor_spend` | vendor_name, invoice_count, total_amount |
| `payment_register` | payment_id, invoice_id, invoice_number, vendor_name, amount, currency, method, status, provider, reference, submitted_at, completed_at |
| `aging_snapshot` | as_of_date, current, days_30, days_60, days_90_plus, total |

Column order is pinned by `tests/test_report_export.py` — finance
imports rely on column position; a reorder breaks downstream
pipelines.

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

The runner (`services/scheduled_reports.run_loop` — wired into
`main.lifespan`) ticks on a timer, calls `list_due_schedules` for
every tenant, then `execute_schedule` per row. Success bumps
`next_run_at` forward by the cadence and clears the error;
failure persists a `[retry N]` marker and bumps the count. After
five consecutive failures the row is auto-disabled so the queue
doesn't loop forever — an operator re-enables from the admin UI
after fixing the underlying issue.

Email-adapter exceptions never leak provider-side details into
`last_run_error` (invariant #7); only the exception class name
is stored.

## Known gaps

- **Received_amount in accruals**: requires a 3-way-match fan-out
  (PO line × GR line). Today returns 0 — flagged in the CFO doc
  and on the dashboard tile. SOC 2 audit ticket open.
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
