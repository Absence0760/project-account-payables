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

## Money serialisation

**Every money field on every `/api/analytics/*` endpoint is an EXACT decimal
string** (`"1500.00"`), never a JSON number — the project's Decimal invariant
applied at the API boundary, so no figure a CFO reads has round-tripped through
a binary float. `app/api/analytics.py::_money` is the module's single
serialiser; route every new money field through it rather than calling `str()`
inline, so this file can't half-migrate the way it did when `/drill/dpo` was
the only corrected endpoint (`../../docs/decisions.md` §32). It formats
**fixed-point**, because `str(Decimal("1E+3"))` is `"1E+3"` — parseable in
Python, and exactly the value a downstream consumer's own parser fumbles.
Trailing zeros are preserved (`"0.00"` stays `"0.00"`); a `None` figure stays
JSON `null`, never the string `"None"`. A source scan in
`tests/test_analytics_money_serialization.py` fails on any bare `str(...)` in
the module, so a second serialiser can't quietly appear.

**What is deliberately NOT a string**, because it is not money and
stringifying it would be a bug wearing compliance's clothes:

| Kind | Fields |
|---|---|
| Day counts | `dpo_current`, `dpo_trend[].dpo`, `drill/dpo` `rows[].dpo`, `cash_conversion_cycle`, `weighted_avg_pay_date_days` |
| Percentages | `supplier_concentration.{top_10_share_pct,top_50_share_pct,largest_vendor_share_pct}`, `drill/spend_concentration` `rows[].share_pct`, `fraud_rate_trend[].rate_pct`, `rebate_yield.yield_pct`, `forecast_variance` `rows[].variance_pct` |
| Counts | every `count` / `*_count` / `invoice_count` / `exception_count` / `open_exceptions` |

A `null` money field stays JSON `null` (`cash_position.threshold`,
`cash_conversion_cycle`) — "not set" is not `"0"`.

**Consumers.** `frontend/src/lib/types/analytics.ts` types these as
`MoneyString`; render them with `<Money>` / `formatMoney` and get a number out
of one only via `parseMoneyForLayout` (chart geometry + ordering — see
`frontend/CLAUDE.md` § Money formatting). The Flutter app's
`mobile/lib/models/cash_flow.dart` keeps them as display strings through
`moneyToDisplay`, which passes a string through verbatim and still stringifies
a legacy JSON number, so an app build older than this change keeps rendering.

## Operational metrics (dashboard)

Existing fields stay: `pipeline`, `vendor_spend`, `aging`,
`monthly_trend`, `upcoming_payments`, `touchless_rate`, `total_*`.

- `vendor_spend` — top 10 vendors by spend, excluding `rejected` invoices.
  Rolled up into the org's reporting currency via
  `currency_conversion.vendor_rollup_to_reporting_currency` — a vendor billing
  in more than one currency is converted invoice-by-invoice and then summed,
  not aggregated with a naive cross-currency `SUM(amount)` (that used to add a
  USD invoice and a EUR invoice as if they were the same currency). Same
  helper backs the CFO supplier-concentration tile, its drill-through, the
  `vendor_spend` CSV export, and the scheduled report — see
  `docs/multi-currency.md` § Per-vendor rollups.
- `total_amount` — the "Total Amount" KPI on the web dashboard (`routes/+page.svelte`).
  A **naive sum across every invoice in the tenant, regardless of status or
  date** — no filter at all. This is a different population from the CFO
  `total_spend` below (windowed + excludes rejected), even though both read
  like "how much have we spent": a rejected invoice, or one still sitting at
  `new`, counts toward this figure but not toward `total_spend`, and this
  figure has no date bound while `total_spend` is windowed to `period_days`.
  The web label spells this out (`Total Amount (All Invoices)`) precisely so
  the two aren't misread as the same number. See
  `tests/test_analytics_rejected_exclusion.py` for a regression test pinning
  the contrast.
- `aging` — open-invoice exposure bucketed by **days past the due date**:
  `current` (not yet due), `days_30` (1-30), `days_60` (31-60), `days_90`
  (61-90), `days_90_plus` (90+). The same five buckets back the
  `aging_snapshot` CSV export and the emailed scheduled report. Covers the
  SAME population as the CFO `accounts_payable_balance` (F-4) — which has no
  `due_date` filter — so an open invoice with a null `due_date` buckets as
  `current` (unknowable, so not overdue) rather than being silently dropped;
  otherwise the bands stop summing to the balance the moment one open invoice
  is missing a due date.
- `touchless_rate` — straight-through-processing rate. **Definition: the share
  of invoices that PASSED REVIEW without a human touching them, out of every
  invoice that provably finished review**, excluding rows a CSV import
  planted. See § Touchless rate — what the number means below; that section
  also records the two times this definition changed and which way the number
  moved each time.
- `monthly_trend` — invoice count + amount (+ `reporting_amount`) per calendar
  month, for the **last six calendar months**. The window is anchored to the
  1st of the month five months back, not `today - 180 days`: a rolling day
  window against a calendar-month `GROUP BY` never lines up, so the oldest bar
  was a partial slice of a month — sometimes a seventh, stub bucket of a
  fortnight's data — that reads as a spend collapse and shifts every single day
  as the window slides.
- `upcoming_total_amount` — server-computed total across the same rows behind
  `upcoming_payments` (summed in `Decimal`, converted to `float` exactly once
  at the response boundary). Callers (the mobile dashboard) must read this
  field directly rather than folding `upcoming_payments[].amount` themselves —
  summing already-serialized floats client-side accumulates rounding drift.

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
- `discount_capture` — eligible / captured / **missed / pending** counts and
  amounts, with `capture_rate_pct`. Eligibility comes from
  `PaymentSchedule.discount_percent`; capture is determined by
  whether the matching completed Payment's `completed_at` ≤
  `discount_date`.

  Three buckets, not two. An eligible invoice is `missed` only once its
  discount window has **elapsed** without being captured; while the deadline is
  still ahead (or the schedule carries no `discount_date` at all) the row is
  `pending` — still fully on the table. This surface was the only one of the
  consumers of these economics that never applied the elapsed-window gate
  (`bucket_outflows`' discount-eligible amount, the `early` what-if scenario,
  `discount_optimizer.optimize` and `discount_offers._tier_achievable` all
  did), so every newly-scheduled discount landed straight in a growing pile of
  "forgone savings" that had not been forgone. The rule now has one owner,
  `analytics.discount_window_open`.

  `capture_rate_pct` is over the DECIDED population (`captured + missed`) and
  is **`null`, never `0`**, when nothing has been decided — with
  `insufficient_data: true` beside it. "We have not missed a discount yet" and
  "we captured none of the discounts we could have" are opposite facts, and 0%
  renders as the bad one (`docs/decisions.md` §34).

  Money: `captured_amount` / `missed_amount` / `pending_amount` are per-row
  FACE values (a share of `Invoice.amount`) and mix currencies the moment one
  eligible invoice is foreign. The figures to render are
  `*_amount_reporting`, denominated in the response's own
  `reporting_currency` and resolved by `currency_conversion.reporting_amount_for_row`;
  a row with no usable rate lock contributes face value and is counted on
  `unconverted_count` rather than dropped (`docs/decisions.md` §35).

  **Frontend surface**: the dashboard (`frontend/src/routes/+page.svelte`) —
  an `Early-payment discounts` card rendering all three buckets with their
  reporting-currency amounts, plus a `Discounts captured` KPI card. Typed as
  `DashboardDiscountCapture` in `frontend/src/lib/types/analytics.ts`.

  Three contracts the UI encodes, each of which would reintroduce a defect the
  backend fix removed if a later slice "simplified" it:

  - **`unconverted_count` renders WITH the figure, not in a tooltip.** A
    non-zero count means the amounts mix currencies, so the card carries a
    `role="alert"` line (`[data-testid="discount-capture-unconverted"]`) and
    the KPI card's `sub` qualifier carries the same fact — outranking the
    capture rate there, because a rate is context while an unconverted count
    means the headline is partial. Matches how `/cfo`'s cash-position card
    presents its own `unconverted_count`.
  - **The amounts are labelled with the payload's OWN `reporting_currency`**,
    via a local `fmtIn` — never the separately-fetched org-settings currency
    the other dashboard KPIs use, which would let the page print "no exchange
    rate into GBP" above a column of `$`. Same rule, same name, as `/cfo`.
  - **`insufficient_data` renders as its own sentence, never `0%`.** "No
    discount window has closed yet" and "we captured none of them" are
    opposite facts.

  E2E: `frontend/tests-e2e/dashboard/discount-capture.spec.ts` (stubs the
  dashboard response so the pending bucket and the partial-figure disclosure
  are actually on screen — a seeded tenant reliably produces neither).

  **`POST /api/analytics/forecast_variance` now renders the same disclosure**
  — see [Forecast vs actual — the entry surface](#forecast-vs-actual--the-entry-surface)
  below. The three surfaces (this card, `/cfo`'s cash position, `/cfo`'s
  forecast-variance panel) share ONE idiom on purpose: a `role="alert"` line
  placed above the amounts it qualifies, naming the count and the currency it
  could not be expressed in. There is no fourth treatment, and a tooltip is not
  one of them.

### Touchless rate — what the number means

`touchless_rate` is a claim about **how much work the machine did instead of a
person**. That makes its numerator's *population* — not just its arithmetic —
part of the metric's meaning, so it is stated here and encoded once, in
`services/analytics` (`TOUCHLESS_CLEARED_STATUSES` /
`TOUCHLESS_REVIEW_EVIDENCE_STATUSES` / `TOUCHLESS_BOUNCED_STATUSES` /
`compute_touchless_rate`). The hand-written copy that used to live in
`api/dashboard` had already drifted once — `sending_to_erp` is reachable ONLY
from `approved`, yet it appeared in neither leg, so an invoice sitting in the
ERP export hop dropped out of a metric it had already earned a place in,
understating the rate on exactly the tenants whose ERP export is slow.

**The definition: "passed review without human touch."**

| Leg | Statuses | Rule |
|---|---|---|
| Cleared (numerator + denominator) | `approved`, `sending_to_erp`, `sent_to_erp`, `posted_in_erp`, `payment_scheduled` | Status alone is proof — every `VALID_TRANSITIONS` edge into these originates at `approved`, and every writer of `approved` stamps `Invoice.approval_date`. |
| Ambiguous (`TOUCHLESS_REVIEW_EVIDENCE_STATUSES`) | `done`, `paid`, `failed` | Counts as cleared **only** with the durable `Invoice.approval_date` stamp. Without it, the invoice is in NEITHER leg. |
| Bounced (denominator only) | `rejected` | A human sent it back. Cannot be evidence-gated — nothing ever writes an approval stamp on a rejection, and the rejected row IS the evidence a human touched it. |

**And one exclusion that cuts across every row of that table:** an invoice
carrying the `meta["imported"]` provenance marker is subtracted from whichever
leg its status would have put it in — numerator and denominator alike. See
§ Imported rows are outside the metric below.

Why the ambiguous three need evidence:

- **`done`** — `new → done` is a legal transition that skips approval outright,
  and it is the default landing status of the Day-0 CSV importer
  (`services/csv_import`), which bypasses the workflow engine entirely.
- **`paid`** — normally only reachable from `payment_scheduled`, but
  `csv_import._IMPORTABLE_INVOICE_STATUSES` allows it too, for the same Day-0
  historical migration.
- **`failed`** — `VALID_TRANSITIONS` reaches it BOTH from `pending` (extraction
  failed, never reviewed) and from `sending_to_erp` (approved, then the ERP
  export blew up). This leg predates the others and is unchanged.

#### Why "passed review", not "reached a terminal state"

The alternative reading — *reached a terminal state without human touch* —
would keep counting the `new → done` shortcut and the imported historical rows,
because nobody touched those either. It was rejected because the metric is read
as an automation KPI: it is quoted to leadership as evidence the platform is
doing the approving. An invoice that never entered review is not evidence the
machine approved it; it is evidence the invoice was never approved at all.
Counting it inflates precisely the figure being trusted, and it inflates it
hardest for the tenant that just migrated ten thousand historical invoices on
day one — the tenant with the *least* automation to show.

The symmetric mistake is also avoided: a never-reviewed invoice is out of the
**denominator** too, not parked in the bounced leg. Counting it as
"finished review and did not clear" would deflate the rate just as dishonestly.
This is the same rule `failed` has always followed.

#### Imported rows are outside the metric — both legs

The evidence gate above fixes the NUMERATOR. The denominator had the mirror of
the same problem, and evidence cannot fix it: a CSV-imported `rejected` row
sits in the bounced leg as though a reviewer *here* had sent it back, deflating
the rate exactly as imported `done` rows used to inflate it — but nothing ever
writes an approval stamp on a rejection, so gating that leg would zero the
bounced population outright rather than exclude the imports.

**Provenance settles it instead of status.** `services/csv_import` stamps
`meta["imported"] = {"at": …, "source": "csv_import"}` on every invoice row it
creates (see `backend/docs/csv-import.md` § Import provenance). The dashboard
counts marked rows per status and passes them to `compute_touchless_rate` as
`imported_pipeline`, which subtracts them from the cleared, ambiguous and
bounced legs alike; the evidence query excludes them too. Status could never
have done this job — `done`, `paid` and `rejected` are each reachable both by
import and natively.

The reasoning is the same one that put never-reviewed invoices in neither leg:
the metric describes work **this platform** did. A migrated historical row is
evidence neither for nor against that, in either direction.

`imported_pipeline` is a REQUIRED keyword argument for the same reason
`review_cleared_count` is — a caller still on the old signature raises
`TypeError` rather than quietly publishing a rate whose denominator is padded
with somebody else's migrated history.

##### What this does NOT fix

Rows imported **before** the marker shipped carry no key and stay in the
population, on both legs, exactly as they are today. There is **no backfill**,
and there will not be one: absence of the marker means "we do not know", and
stamping a historical row on an inference (its status, its creation date, the
absence of a workflow instance) is precisely the guessing this change exists to
replace. A tenant that migrated before the marker and wants a clean figure has
to wait for the imported cohort to age out of the reports it reads, or exclude
it by date at the query.

Nor does the marker claim to catch every non-native row: it covers the CSV
importer, which is the only bulk path that plants invoices around the workflow
engine today. A future backfill tool (ERP history, a migration script) must
stamp the same key — `IMPORT_PROVENANCE_KEY`, with its own `source` — or its
rows will read as native.

#### The provenance exclusion MOVES it again — direction depends on the mix

The evidence gate moved `touchless_rate` **downward** (next section). The
provenance exclusion moves it again for any tenant that has imported since the
marker shipped, and the **direction depends on that tenant's mix**: dropping
imported `rejected` rows pushes the rate UP, dropping imported `done` / `paid`
rows that had somehow been counted pushes it DOWN, and a tenant whose whole
population is imported falls to the zero-safe `0.0` — the same answer a brand
new tenant gets, which is the honest one.

Both moves are **definition changes, not automation changes**. No workflow,
auto-approval threshold or routing rule changed, and no invoice moved. A
tenant that has never run a CSV import sees no change at all from this half.
Read a dashboard delta accordingly.

#### The evidence gate MOVED a previously reported number — downward

Before the evidence gate, `done` and `paid` counted as cleared on status alone. Any
tenant that uses the `new → done` shortcut, or that migrated history through
the CSV importer, will see `touchless_rate` **drop** the first time the
dashboard is loaded after deploy. The drop is a **definition change, not a
regression in automation** — no workflow, auto-approval threshold or routing
rule changed, and no invoice moved. A tenant whose invoices all travel the
normal `ready_for_review → approved → …` path sees no change at all, because
every one of those rows carries the approval stamp.

If a dashboard delta needs explaining, the honest sentence is: *the metric
stopped counting invoices that never went through approval.*

## CFO metrics (`GET /api/analytics/cfo`)

Query params: `period_days` (default 365, range 30–730).

Every money field below is an exact decimal string; `dpo_current`,
`dpo_trend[].dpo`, `cash_conversion_cycle`, every `*_pct` and every count are
JSON numbers. See [Money serialisation](#money-serialisation).

Response:
- `total_spend` — invoices dated within the trailing `period_days` window,
  excluding `rejected`. **Not the same population as the dashboard's
  `total_amount`** ("Total Amount (All Invoices)") — see that field's
  description above before wiring a UI that shows both side by side.
- `accounts_payable_balance`, `avg_daily_outflow`
- `dpo_current` + `dpo_trend` (the last 6 **closed** months — the loop walks
  back from the 1st of the current month, so the newest point is the month that
  just ended). See § DPO trend + drill-through below. The `/cfo` chart names
  this window (`CfoMetrics.svelte`'s `cfoMetrics.dpoTrend.hint`, under the
  chart title) so "no data for this month yet" doesn't read as a bug.
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
- `supplier_concentration.{top_10_share_pct, top_50_share_pct, largest_vendor, largest_vendor_share_pct, flagged}` — `flagged=true` iff the largest vendor **reaches or** exceeds 25% (configurable; the boundary is inclusive on purpose — a risk flag that stays dark at exactly the configured limit is the wrong direction to be wrong in). **Every share is computed against the whole period's spend**, never a top-N subtotal: `compute_supplier_concentration` derives its denominator from the list it is handed and takes its own `[:10]`/`[:50]` cuts, so the caller must pass the full vendor set and slice only for display. Passing a pre-sliced top-50 made `total_spend` the top-50 subtotal, inflated `top_10_share_pct` / `largest_vendor_share_pct` (and with them `flagged`), and pinned `top_50_share_pct` at exactly `100.0` on any tenant with 50+ vendors. The same rule governs `/drill/spend_concentration`, whose `total_spend` and `share_pct` are computed before `?limit=` is applied — otherwise `limit` silently rebased both and the drill disagreed with the tile it was opened from. Excludes `rejected` invoices (never real spend) — the SAME population its drill-through and the `vendor_spend` export/scheduled report use, so clicking from the tile into either agrees with the number the CFO started from. Also the SAME reporting-currency rollup as the dashboard's `vendor_spend` (see above) — a vendor's multi-currency invoices are converted before summing, never naively added across currencies
- `fraud_rate_trend` — exceptions / invoices × 100 per month. **`rate_pct` is
  `null` (with `insufficient_data: true`) for a month that booked no
  invoices** — an empty denominator makes the rate not computable, and
  returning `0` reported the most reassuring value on the chart for the one
  shape carrying no information at all. It did that hardest in the case that
  most warrants a look: zero invoices booked, exceptions raised anyway. Same
  treatment `cash_conversion_cycle` gets for an unknown leg and the adaptive
  feedback surface gets under its minimum sample; `docs/decisions.md` §34. The
  `/cfo` chart renders those months as `—` with a reason line, and draws no bar.
- `rebate_yield.{yield_pct, annualised_rebates, ...}` — **windowed to the same
  trailing `period_days` as `total_spend`**, which is the denominator it divides
  by and the span `months_in_period` describes. The rebate sum carried no date
  predicate at all, so the numerator was every rebate the tenant had ever
  booked: `yield_pct` was lifetime-over-this-window and `annualised_rebates`
  multiplied a multi-year total by 12. A three-year-old tenant with $36k of
  rebates and $100k of spend in the last 30 days reported a 36% yield and a
  $432k annual run-rate against a truth of ~1% and ~$12k. Filtered on
  `CardRebate.created_at` (when the rebate was booked); the `period` column is a
  display label, not a filter key.

### DPO trend + drill-through — one population, one calculation

`dpo_trend` (the chart) and `GET /api/analytics/drill/dpo?months=N` (the
drill-through a CFO opens to explain a spike in it) answer the same question at
two resolutions, so they must never disagree. They now share both halves:

- **The snapshots** come from `api/analytics.py::_monthly_dpo_snapshots` — the
  only place the monthly `{month, accounts_payable, cogs}` SQL lives. It states
  the `rejected` exclusion once (matching the headline `total_spend`, so the
  COGS proxy isn't inflated relative to the DPO computed from it) and takes the
  open-AP population from the canonical `OPEN_AP_STATUSES`, not a hand-copied
  status list.
- **The arithmetic** comes from the pure `services/analytics.py::compute_dpo_trend`.

They used to be two hand-written copies of the same loop, and the copies had
already drifted: the chart excluded `rejected` invoices from COGS, the
drill-through summed every status — so a $9 000 rejected invoice next to a
$1 000 approved one made the drill-through report 3.0 days where the chart it
was opened from showed 30.0. Same failure shape as the supplier-concentration
tile vs. its drill-through (issue #126). Pinned by
`tests/test_analytics_rejected_exclusion.py`.

`/drill/dpo` serializes `accounts_payable` and `cogs` — money — as **exact
decimal strings**, and `dpo` as a JSON number, because it is a day count. That
split is now the whole module's rule, not this endpoint's exception: see
[Money serialisation](#money-serialisation). (`/drill/dpo` was corrected first,
in isolation, because it had no shipped consumer — `../../docs/decisions.md`
§32.)

Drill-through (money as exact decimal strings here too — see
[Money serialisation](#money-serialisation)):
- `GET /api/analytics/drill/spend_concentration?period_days=N&limit=N` —
  `rows[].amount` + `total_spend` are money; `share_pct` is a percentage and
  `invoice_count` a count, so both stay JSON numbers.
- `GET /api/analytics/drill/dpo?months=N`
- `POST /api/analytics/forecast_variance` — body `{"months":
  [{"month": "YYYY-MM", "forecast": "100000"}, ...]}`. Server
  fills in `actual` from completed payments and returns
  `{reporting_currency, rows: [{month, forecast, actual, variance,
  variance_pct, unconverted_count}, ...]}` — the three amounts money,
  `variance_pct` a percentage, `unconverted_count` a count.
  Forecasts are NOT persisted — the CFO pastes from their FP&A
  tool.
  - **`actual` is resolved into the org's reporting currency** via
    `currency_conversion.payment_reporting_amount_sql`, not summed off raw
    `Payment.amount` — that column is denominated in the INVOICE's currency, so
    one foreign payment turned the month's actual into a two-currency mixture
    and then compared it against a forecast typed in a single currency. A
    payment neither rung can express is EXCLUDED and counted on
    `unconverted_count`, never added at face value: a variance is a number
    someone acts on, so it must read as the floor it is (`decisions.md` §35).
    Same resolver the payments summary, the AML trailing-spend gate and the
    1099 filing use.
  - **A malformed `month` is a `422`, not a `500`.** The guarded parse used to
    cover `int(...)` but not the `date(year, mon, 1)` it fed, so a
    caller-supplied `"2026-13"` (or `"2026-00"`) escaped as a bare `ValueError`.
    Both halves are now inside one `try`, and every month-shape rejection on
    this route answers `422` rather than a mix of `400` and a crash.

#### Forecast vs actual — the entry surface

`frontend/src/routes/cfo/ForecastVariancePanel.svelte`, rendered on `/cfo`
**outside** the cash-flow `{#if}` (the reasoning the budget rollup and the
scheduled-reports panel already follow: it takes none of that page's controls,
issues its own request, and a failed cash-flow load must not hide the only
surface that renders this endpoint's disclosure). Role-gated `admin | cfo`
inside the panel, matching `_CFO_ROLES` on the endpoint and the route's own
gate.

**It needs a form because the endpoint is a POST with a body.** The forecast is
the CFO's own figure set, pasted from their FP&A tool, and nothing is
persisted — so there is no saved forecast to `GET` and every visit starts from
an empty editor. A `Modal` holds one row per month: an `<input type="month">`
(which yields exactly the `YYYY-MM` the route parses, so the month half needs
no repair) and a free-text amount.

- **The amount is raw decimal text, validated ONCE at submit and refused with a
  toast** — never `parseFloat`ed per keystroke, never repaired. `$1,200.50`,
  `1 200`, `1.2e5` and a trailing `.` are all refused rather than stripped down
  to a figure the CFO never typed; a month typed without an amount refuses the
  whole submit instead of being silently sent as `0` (which would make the
  variance equal the entire actual outflow and report a fabricated `0%`). The
  shape check is `utils/moneyInput.ts::normalizeMoneyInput` — a regex, never
  `Number` — so the string on the wire is the string that was typed.
- **The disclosure renders with the figures.** `unconverted_count` is folded
  across months (a COUNT may cross currencies; the amounts beside it may not)
  into a `role="alert"` line, `[data-testid="forecast-variance-unconverted"]`,
  placed ABOVE the table it qualifies and naming the response's own
  `reporting_currency`. The month carrying the exclusion also says so on its own
  row. Identical treatment to `/cfo`'s `unconverted-outflows` and the
  dashboard's `discount-capture-unconverted`.
- **`variance_pct` is never rendered as `0%` where it is not computable.** The
  route emits `Decimal("0")` whenever `forecast <= 0`, which on screen reads as
  "we landed exactly on plan" — the most reassuring statement available over the
  one row carrying no information. `variancePctLabel` returns `null` for a
  non-positive forecast and the cell renders its own not-applicable state, the
  same rule as the fraud-rate trend's `insufficient_data` and the budget
  rollup's `null` utilization (`../../docs/decisions.md` §34).
- Pure derivations live in `frontend/src/routes/cfo/forecastVarianceSummary.ts`
  (`collectForecastEntries` / `unconvertedTotal` / `variancePctLabel` /
  `varianceTone`), unit-tested beside the route like `budgetRollupSummary.ts`;
  the request helper is `frontend/src/lib/api/analytics.ts`. No money
  arithmetic happens client-side — the backend owns `variance` and its
  percentage, in `Decimal`.

E2E: `frontend/tests-e2e/cfo/forecast-variance.spec.ts` (stubs the POST so the
disclosure is actually on screen — a seeded tenant reliably produces no
unconvertible payment, which is the state that let this ship with no consumer;
also asserts the typed decimal string reaches the wire verbatim and that an
unreadable amount sends no request at all).

**Frontend surface**: `frontend/src/lib/components/analytics/CfoMetrics.svelte`,
embedded in `/cfo` below the forecast/what-if/cash-position panels (a
self-fetching component mirroring `ByEntityBreakdown` — its own `GET
/api/analytics/cfo?period_days=` call, own loading/error state). Renders a KPI
row (DPO current, cash conversion cycle, AP balance, rebate yield %), the DPO
6-month trend as a bar chart, an accruals breakdown, supplier concentration
(with the flagged-vendor banner), the fraud-rate trend, and the unrealized-FX
table when available. `/forecast_variance` has its own surface on the same route
(`routes/cfo/ForecastVariancePanel.svelte` — see above); the two remaining
drill-throughs (`/drill/spend_concentration`, `/drill/dpo`) are not yet wired
into the UI — that stays a future slice. Filed as #236.

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

Every money field below is an exact decimal string; `dpo_current`,
`dpo_trend[].dpo`, `cash_conversion_cycle`, every `*_pct` and every count are
JSON numbers. See [Money serialisation](#money-serialisation).

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
`Decimal` end-to-end and serialises as an **exact decimal string** (see
[Money serialisation](#money-serialisation)). No writes, no audit rows, no new
migration — pure aggregate math, like `forecast_variance`.

**Committed vs pending status sets** (the rest — `rejected`, `paid`,
`done`, `failed` — are excluded):

- **committed** (firm AP commitment): `approved`, `sending_to_erp`,
  `sent_to_erp`, `posted_in_erp`, `payment_scheduled`.
- **pending** (in-flight pipeline, lower-certainty projection): `new`,
  `pending`, `ready_for_review`. Drop with `include_pending=false`.

`GET /cashflow_forecast?granularity=day|week|month&horizon_days=N&include_pending=bool`
— per-period `{scheduled_amount, committed_amount, pending_amount,
discount_eligible_amount, count, unconverted_count}` plus a `totals` rollup.
Weeks are Monday-anchored. Default `granularity=week`, `horizon_days=90`.

### One commitment row per invoice, whatever its schedule count

`_commitment_rows` outer-joins `payment_schedules` for the authoritative due
date + early-pay discount terms. That join was **un-deduped**: it returns one
row per schedule, and the loop appends a commitment row for each — so an invoice
carrying two `PaymentSchedule` rows was counted **twice, at its full amount**.
Because this one query feeds the forecast, the cash-position curve, the what-if,
every copilot planning tool and the `plan_id` hash, a single double-count
overstates projected outflow on all of them at once, with nothing on any surface
to show it.

It is unreachable today only because nothing but `scripts/seed.py` constructs a
`PaymentSchedule` — an accident of the current feature set, not a guarantee.

The query now takes `DISTINCT ON (invoice_id) … ORDER BY invoice_id,
created_at DESC, id DESC` — the LATEST schedule, mirroring
`discount_auto_trigger._resolve_due_date`'s existing `ORDER BY created_at DESC
LIMIT 1` on the same table, so the discount engine and the cash forecast cannot
disagree about an invoice's due date. `created_at` defaults to
`transaction_timestamp()`, so rows written in one commit share a timestamp and
"latest" is genuinely undefined between them; the `id` tiebreak only makes that
case **deterministic** (a copilot `plan_id` must not flap between reads), it
does not invent an ordering. Guard:
`tests/test_cashflow_forecast_api.py::test_two_payment_schedules_do_not_double_count_the_invoice`.

### `unconverted_count` — the outflow-side currency caveat

Every commitment row is expressed in the org's **reporting** currency via the
rate locked on the invoice (`_commitment_rows` → `reporting_amount_for_row`).
A foreign invoice with no usable lock is included at **face value** — dropping
it would understate the outflow — and flagged. That flag is now surfaced as
`unconverted_count`: per period on `/cashflow_forecast` and `/cash_position`,
and as a total on all three cash endpoints (and on the copilot's
`get_cashflow_forecast` / `get_cash_position` / `run_payment_whatif` results).

It matters because the face value is a *different currency's* number sitting
in a reporting-currency total. One unconverted ¥10,000,000 invoice drags a
$250,000 opening balance to a projected −$9.75M — and the shortfall sweep
emails that to the finance leaders. The flag was computed on every row and read
by nobody, so the number arrived with nothing to contradict it. Non-zero means
the totals mix currencies and the curve is not a figure to act on until the
conversion is resolved; it is the outflow-side twin of `cash_position`'s
`opening_balance_provider_skipped: "currency_mismatch"`, which already guarded
the opening-balance half of the same equation.

It is a **count**, not money — a JSON number, listed in
`tests/test_analytics_money_serialization.py`'s `NUMERIC_FIELDS`.

`GET /cashflow_whatif?granularity=…&horizon_days=N&grace_days=N` —
compares three payment-timing scenarios, each with `total_outflow`,
`total_discount_captured`, amount-weighted `weighted_avg_pay_date_days`,
and bucketed `periods`. `weighted_avg_pay_date_days` is a day count, not
money, and stays a JSON number:

- `on_time` — pay on `due_date`, full amount.
- `early` — pay on `discount_date` when that window is **still open**
  (`discount_date >= today`), net of `discount_percent` (the captured
  discount is reported separately); rows without a discount — and rows whose
  window has already **elapsed** — fall back to the due date at full amount.
- `late` — pay `due_date + grace_days`, full amount, discount forfeited.

The elapsed-window guard matters because the source rows are bounded on their
**due** date only: an in-horizon invoice on `2/10 net 60` terms routinely
carries a `discount_date` weeks in the past. Claiming it overstated
`total_discount_captured`, timed the outflow on a date before `today` (buckets
entirely inside a period that has already closed, and a **negative**
`weighted_avg_pay_date_days`), and invited a payment run funded against savings
that were no longer available. The rule matches
`services/discount_optimizer.optimize`, which has always treated an elapsed
deadline as not capturable — so the what-if card and the optimizer agree about
which discounts are still on the table.

`GET /cash_position?granularity=…&horizon_days=N&opening_balance=STR&min_balance_threshold=STR&seed_balance=bool`
— running balance carried forward per period
(`closing = opening − outflow`; receivables/inflows aren't modelled in an
AP-only product). Periods that close below the effective threshold are flagged
(`below_threshold`) and collected in `breaches[]` with the `shortfall`. Money
params are parsed as `Decimal` strings (never floats); garbage → 400. A
`threshold` of `null` on the response means none is set — deliberately not
`"0"`, which would read as "alert on any positive balance".

**Opening balance — resolution order** (first hit wins; the chosen source is
echoed as `opening_balance_source`):

1. `opening_balance` query param — explicit bring-your-own override →
   `"explicit"`.
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
| `vendor_spend` | vendor_name, invoice_count, total_amount, currencies |
| `payment_register` | payment_id, invoice_id, invoice_number, vendor_name, amount, currency, method, status, provider, reference, submitted_at, completed_at |
| `aging_snapshot` | as_of_date, current, days_30, days_60, days_90, days_90_plus, total. `as_of_date` defaults to `utc_today()` — neither caller passes `snapshot_date`, and both bucket against a UTC `today`, so a local-time default labelled the file with one date while the buckets were computed as of another |
| `cashflow_forecast` | period, period_start, period_end, scheduled_amount, committed_amount, pending_amount, discount_eligible_amount, count |
| `expense_register` | date, merchant, category, amount, currency, gl_code, payment_method, status, report_number |

`cashflow_forecast` is forward-looking — it takes `granularity` +
`horizon_days` (not `period_days`) and runs the same forecast query as the
JSON endpoint.

`vendor_spend`'s `total_amount` is the org's reporting-currency rollup (see
above), not a raw `SUM(amount)` — `currencies` lists every distinct original
invoice currency that rolled into the total, so a mixed-currency vendor's row
is auditable (e.g. `"EUR, USD"`) rather than looking like a same-currency sum.

### `Row` is not a tuple — duck-type the joined exporters

`payment_register` and `expense_register` take **joined** rows, and every real
caller (this endpoint and the scheduled-report runner) hands them `rows.all()`
— a list of `sqlalchemy.engine.row.Row`. A `Row` implements `Sequence` but is
**not** an `isinstance(tuple)` in SQLAlchemy 2.x, so an exporter branching on
`isinstance(row, (tuple, list))` silently misses every production call and
falls into its attribute-access fallback, where a `Row` has none of the ORM
attributes and `getattr(..., default)` swallows each miss. That shipped twice:
`vendor_spend` exported a blank `total_amount`, and `payment_register` exported
a header plus one **all-blank row per payment** (a hardcoded `USD` its only
content). Both now duck-type on `__getitem__`, so `Row` and `tuple`/`list`
share the positional path. Any new joined exporter must do the same, and its
test must exercise a genuine `Row` (`tests/test_report_export.py::_sqlalchemy_rows`
builds one with no database) — plain-tuple fixtures cannot catch this.

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
— wired into `main.lifespan`, gated by `FEOH_SCHEDULED_REPORTS_ENABLED`,
disabled by default so local dev / tests never email) ticks every
`FEOH_SCHEDULED_REPORTS_TICK_SECONDS`. Each tick `run_scheduled_reports_once`
fans out across every tenant DB (`_sweep_tenant`), calls `list_due_schedules`,
then `execute_schedule` per due row — one tenant's failure never halts the
sweep.

**Delivery is per-recipient**, and `last_run_status` has three values:

| Status | When | `next_run_at` | Counts toward auto-disable |
|---|---|---|---|
| `success` | every recipient took the report | bumped by the cadence | — (clears the chain) |
| `partial` | some took it, some didn't | **bumped** | no |
| `failure` | generation failed, no recipients configured, or NOBODY took it | untouched → next tick retries | yes (`[retry N]`) |

A `partial` still advances `next_run_at` because the alternative is worse: a
retry redelivers to everyone who already has the report. The whole loop used to
sit inside one `try`, so a bad address at position 2 of 5 skipped positions 3-5
entirely *and* held `next_run_at`, re-sending to position 1 on each of the five
retry ticks before the auto-disable — while 3-5 never received it once and then
lost the schedule. Each address is now attempted independently, and a `partial`
is deliberately **not** a strike: disabling the schedule over one unreachable
recipient punishes the ones it is still reaching. The durable fix for a
persistently bad address is an operator correcting or removing it — the failure
count on `last_run_error` is what tells them to.

After five consecutive `failure`s the row is auto-disabled so the queue doesn't
loop forever — an operator re-enables it after fixing the underlying issue, via
`PATCH /api/analytics/scheduled-reports/{id}` (§ CRUD below). The tenant sweep
counts a `partial` as a failed run for `sweep_health`
(`GET /api/health/sweeps`), so an undelivered recipient shows up there rather
than rounding to "healthy".

Email-adapter exceptions never leak provider-side details into
`last_run_error` (invariant #7); only the exception class name — and, for a
partial, the failed/total **counts** — are stored.

### `next_run_at` advances from the DUE slot, not the tick

`execute_schedule` used to set `next_run_at = compute_next_run(cadence, now)` —
from the moment the sweep happened to run it. The sweep ticks hourly
(`FEOH_SCHEDULED_REPORTS_TICK_SECONDS`), so a "daily 09:00" report was typically
picked up at 09:37 and rescheduled for 09:37 tomorrow, then 10:14, then 11:02:
every run landed later than the last and the report walked all the way around
the clock inside a month.

`advance_next_run(cadence, scheduled_for=…, now=…)` anchors on the slot the run
was **due** at, so 09:00 stays 09:00 no matter when the tick fires. A missed
window (process down, tenant unreachable, the schedule disabled and re-enabled)
is caught up in **whole cadence steps** to the first slot strictly after `now` —
never one send per skipped period. That is deliberate: the report is a periodic
snapshot of *current* state, so a backlog burst would deliver N identical
copies. The schedule simply resumes on its own grid.

`compute_next_run` remains, for seeding a brand-new schedule (one step, no
catch-up). Both go through `known_cadences()` / `_step`, which is also what the
CRUD surface validates against, so there is no second list.

**A month is counted in months, not in 30 days.** `monthly` used to step by
`timedelta(days=30)`, and anchored on a late-month day that walks the grid off
the calendar: 31 Jan → 2 Mar → 1 Apr → 1 May → 31 May → 30 Jun. February
received **no report at all**, and the day of month slid five days inside half a
year — on a schedule whose entire contract is "once a month". `monthly` now
lives in its own `_CADENCE_MONTHS` registry and steps through the shared
`billing.period.add_months` (the tested owner of clamped month arithmetic on a
`datetime` — reused rather than re-implemented a fourth time), so every calendar
month gets exactly one run and the time of day still holds.

The day of month clamps to the target month's length (31 Jan → 28/29 Feb), and
because the next anchor is the stored slot, a 31st schedule settles on the 28th
after its first February and stays there. Holding the 31st would need an anchor
column `scheduled_reports` does not carry; a stable day is a much better answer
than the drift it replaces. Catch-up for `monthly` counts whole months from the
anchor — a schedule dormant for three years still resolves to a single next
slot, not one send per skipped month.

### "Today" is UTC

`_materialise_rows` slices each report's `period_days` window — and the
`cashflow_forecast` / `aging_snapshot` branches their `today` — from
`app.utils.dates.utc_today()`, not `date.today()`. `date.today()` resolves in
the *server's* local timezone; the `/analytics` export endpoints and the
cash-flow copilot resolve UTC. On a UTC container they agree, so the mixture was
latent — but on any other host an emailed snapshot and the API export of the
same report disagree at the day boundary, which is a reconciliation problem, not
a cosmetic one.

The rule is no longer local to this stack. `utc_today()` is the one definition
for the backend, and the AP surfaces converged on it too — the early-pay
discount cutoff, the past-due and future-invoice-date fraud flags, the
recurring-template period key (which IS that sweep's idempotency key), and the
regulated `Invoice.approval_date` on all three approval paths.
`tests/test_utc_today.py` holds the allowlist of converged modules and
AST-scans each one, failing on `date.today()`, `datetime.today()`,
`datetime.date.today()` **and** `datetime.now().date()` — the last is naive-now,
so a local date under a name that reads deliberate, and a purely `today`-shaped
scan would never see it. Adding a module to the allowlist is how a conversion
lands; the guard is what stops one sliding back.

### CRUD — `/api/analytics/scheduled-reports`

`app/api/scheduled_reports.py`. The runner's input surface; before it existed a
row could only be created by direct SQL, `list_due_schedules` returned `[]` on
every tick forever, and the 5-strike auto-disable was a one-way door.

| Method | Path | Roles |
|---|---|---|
| `GET` | `/api/analytics/scheduled-reports` | admin, cfo |
| `POST` | `/api/analytics/scheduled-reports` | **admin only** |
| `GET` | `/api/analytics/scheduled-reports/{id}` | admin, cfo |
| `PATCH` | `/api/analytics/scheduled-reports/{id}` | **admin only** |
| `DELETE` | `/api/analytics/scheduled-reports/{id}` | **admin only** |

Mutations are admin-only, above the `admin`/`cfo` gate the rest of `/analytics`
uses: a schedule is a standing instruction to email a CSV of the tenant's AP
spend to an arbitrary address on a recurring basis, with no review of any
individual send. That is a data-egress control, not a reporting preference.

- **Validation is against the runner's own registries** (`app/schemas/
  scheduled_report.py`): `report_type` ∈ `report_export.EXPORTERS`, `cadence` ∈
  `known_cadences()`. Neither is restated. An out-of-registry `report_type`
  raises on every tick and burns the auto-disable without ever sending; an
  unknown `cadence` silently falls back to daily, so a "yearly" row would have
  emailed 365 times a year.
- **Recipients** are shape-checked (the same permissive regex `api/signup.py`
  uses — no `email-validator` dependency), case-insensitively de-duped (a
  duplicate would double-send), and bounded at 20 with at least one required.
- `next_run_at` is optional; omitted it defaults to *now*, so the schedule fires
  on the next tick and the operator sees it work. Because `advance_next_run`
  then holds that time-of-day, pass an explicit value to pin e.g. 09:00. A value
  in the past is legitimate and is not rejected — the row is immediately due,
  then catches up in whole steps.
- `PATCH {enabled: true}` on a row the 5-strike rule disabled also **clears the
  `[retry N]` marker**. `_mark_failure` reads that prefix to count consecutive
  failures, so re-enabling without clearing it means the next failure lands at
  retry 6 and disables the row again immediately — indistinguishable, to the
  operator, from the re-enable not having worked.
- **Tenant-scoped via `get_tenant_db`, deliberately NOT entity-scoped.**
  `ScheduledReport` has no `entity_id`, and `_materialise_rows` applies no
  entity filter to any of its six report types — the emailed CSV is whole-tenant
  by construction. Stamping an entity on the schedule row would advertise a
  scope the delivered file does not honour. Making it real means entity-filtering
  the materializer *and* a migration; that is its own slice.
- Every mutation writes a PII-free audit row (`scheduled_report.created` /
  `.updated` / `.deleted`) carrying the recipient **count**, never the
  addresses — the trail is append-only and WORM-shipped, so a corrected
  distribution list could never be redacted out of it.

Tests: `tests/test_scheduled_reports_api.py` (CRUD, RBAC, tenant isolation,
validation, PII-free audit) and `tests/test_scheduled_reports.py` (the runner +
the cadence anchoring).

## Known gaps

- **Cash conversion cycle**: AP-only; DSO + DIO need data we
  don't carry. Returns NULL; the UI renders "needs receivables
  data" rather than a misleading 0.

## Tests

| File | Coverage |
|---|---|
| `tests/test_analytics.py` | Every compute_* function: DPO formula, CCC None-on-missing-legs, working-capital monotonicity, supplier concentration flag threshold, fraud-rate **not-computable** on a zero-invoice month (`None`, never `0`), rebate annualisation, forecast-variance sign convention, processing-time min-sample collapse, approval-bottleneck rollup + unassigned bucket, discount-capture three-way split (open window is `pending`, not `missed`) and its no-decided-rows `None` rate |
| `tests/test_report_export.py` | 11 cases — registry pins all four reports; per-report header column-order pinned; enum-status reads `.value`; missing fields emit empty (not "None"); orphan payment-with-null-invoice still emitted |
| `tests/test_scheduled_reports.py` | 20 cases — cadence delta math; unknown-cadence fallback; happy-path generates → emails every recipient → updates next_run_at; generator-error / empty-recipients / email-adapter-error all persist a failure marker without raising; PII guardrail (no SMTP transport details in `last_run_error`); five-consecutive-failures disables the row, first failure leaves enabled alone; **per-recipient delivery** — one bad address doesn't block the ones after it, a partial advances `next_run_at` and isn't a strike, a total failure holds `next_run_at` and still attempts every address; **cadence anchoring** — 30 late ticks in a row leave the 09:00 slot at 09:00, a dormant fortnight catches up to ONE next slot rather than 14 sends, an exactly-due slot still moves forward (no busy-loop), a future slot takes exactly one step, a naive `scheduled_for` reads as UTC |
| `tests/test_scheduled_reports_api.py` | 23 cases — CRUD round-trip; the created row is what `list_due_schedules` picks up; `report_type` / `cadence` validated against the runner's own registries (create AND patch); recipient list shape-checked / de-duped / bounded / non-empty; our validator message names no address; RBAC (mutations admin-only, reads admin+cfo, ap_manager/ap_clerk refused); tenant isolation (list, get, patch); PII-free audit rows carrying the recipient COUNT; re-enabling a 5-strike-disabled row clears the stale `[retry N]` marker |
| `tests/test_utc_today.py` | Drift guard — `utc_today()` is the UTC calendar date; an AST scan fails on any `date.today()` / `datetime.today()` / `datetime.date.today()` reappearing in the modules that have converged on it (the cash-flow stack, plus the AP surfaces: discounts, portal, dashboard, payments queue, recurring, workflow, review, extraction, invoice warnings, 1099, Positive Pay, the exporters). The scanner itself is a tested helper — the module-attribute spelling `datetime.date.today()` slipped past the first version, which is how two Positive Pay modules could have been listed as converged while still reading local time, and naive `datetime.now().date()` isn't spelled `today` at all |
| `tests/test_touchless_rate_population.py` | The touchless numerator's POPULATION (as opposed to its arithmetic): a `new -> done` shortcut and a CSV-imported `paid` are in neither leg, a genuinely approved `done` still is, a rejection is denominator-only exactly as before, and the never-reviewed rows leaving the denominator are the ONLY denominator movement. Plus structural guards re-derived from `VALID_TRANSITIONS` and `csv_import._IMPORTABLE_INVOICE_STATUSES`, so a new legal edge or a newly-importable status cannot quietly re-widen the metric |
| `tests/test_dashboard_aggregations.py` | Existing — extended through the new branches via the try/except absorption pattern |
| `tests/test_dashboard_aggregates.py` | Real-Postgres guards for the four aggregates that were each wrong in their own way: `total_paid` vs its converted `total_paid_reporting` counterpart under mixed currency; `discount_capture`'s elapsed-window gate (open window → `pending`, elapsed → still a `missed`) and its reporting-currency amounts + `unconverted_count`; `touchless_rate` counting `sending_to_erp` and an approval-stamped `failed` while ignoring an extraction-failed one, and excluding a `done`/`paid` row that reached its terminal status without ever being approved; `monthly_trend` returning six WHOLE calendar months with no partial oldest bar and no seventh stub bucket (both window shapes pinned against a frozen `utc_today`) |
| `tests/test_analytics_trend_insufficient_data.py` | The two "reported a comfortable number where there was none" surfaces: `compute_fraud_rate_trend` returning `None` + `insufficient_data` for a zero-invoice month (including the zero-invoices-with-exceptions shape) while still reporting a genuine 0%, and end-to-end `null` on the `/cfo` wire; `/forecast_variance` resolving `actual` into the reporting currency under mixed currency, excluding-and-disclosing an unexpressible payment, and answering `422` (not `500`) for `2026-13` / `2026-00` / `2026-99` / `0000-01` / `2026/07` |
| `tests/test_cashflow_balance.py` | Unit — `get_balance` capability (base-class default unsupported; mock deterministic + config override + simulated-unsupported); `fetch_provider_balance` best-effort (mock balance, None on unsupported, swallows adapter error); persisted-threshold resolve/store round-trip + garbage tolerance + key preservation/clear |
| `tests/test_cashflow_forecast_api.py` (cash-position additions) | API — auto-seed opening balance from the mock provider (`source: provider`); `seed_balance=false` skips it; query param beats provider; provider-unsupported falls back to `settings`; persisted threshold applied without a query override; `cash-position-settings` GET/PUT round-trip; negative → 422; RBAC (ap_clerk 403, admin/cfo 200) |
| `tests/test_analytics_money_serialization.py` | Money serialisation — a **structural** guard over `/cfo`, the cash-flow trio, both drill-throughs, `/forecast_variance` and `/by-entity`: every response is walked and any JSON *number* whose key isn't in the declared day-count / percentage / count roster fails, so a new money field added as a float can't land silently. Plus the zero-population `/cfo` response (where `0` and `"0"` both read as "nothing here"), the `null` cash-position threshold, and the exact seeded figures surviving the round trip |
| `tests/test_analytics_by_entity.py` | `/by-entity` — per-entity spend/invoice-count scoping for two entities; `consolidated` equals the cross-entity sum; open-exceptions scope per entity; single-entity tenant returns a coherent one-row breakdown; RBAC (ap_clerk/ap_manager 403, cfo 200); the endpoint ignores `X-Entity-ID` |
