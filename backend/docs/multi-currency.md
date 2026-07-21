# Multi-currency reporting

How the app rolls multi-currency invoices up into one **reporting (base)
currency** for analytics + dashboards, and how it tracks **unrealized** FX
gain/loss on open foreign-currency invoices.

This sits **on top of** the payment-level FX machinery — it does not replace it:

| Layer | Lives in | What it does | When the rate is locked |
|---|---|---|---|
| **Payment FX** (existing) | `services/international_payments.py`, `services/fx_adapters/` | Locks an FX rate on the `Payment` row at submission; computes **realized** gain/loss (`compute_fx_gain_loss`) when a foreign invoice settles. | At payment submission |
| **Reporting FX** (this doc) | `services/currency_conversion.py` | Converts each invoice's `amount` into the org reporting currency, materializes the rate on the invoice row, rolls multi-currency volume into one total, computes **unrealized** gain/loss on open invoices. | When the invoice is created / mutated |
| **Expense FX** | `services/expense_currency.py` | Converts each expense line into its **report's** currency (`expenses.converted_*`) so a report total isn't a cross-currency sum, and the report total into the **org reporting** currency (`expense_reports.reporting_*`) so the CFO threshold compares like with like. | Line: on attach / amount-or-currency edit / report-currency change. Report: at submit |
| **Expense-policy thresholds** | `services/expense_policy.py` | Compares an expense against a policy's money thresholds **in the policy's `threshold_currency`**, so a €200 expense isn't judged against a USD 100 limit as bare numbers. | Never — it locks nothing. It **reuses** the line's existing lock (row above) and fails closed when none bridges the two currencies. |

Same three rules across every layer that locks a rate — **`Decimal` only**,
**rate locked and persisted on the row**, **never re-fetched at read time**. See
`international-payments.md` for the payment side and `expense-management.md`
§ Multi-currency reports for the expense side.

The fourth row is the exception that proves the rule: an expense **policy** is a
standing rule, not a transaction, so there is no honest moment at which to lock
a rate onto it (one locked when the rule was written would be stale for every
expense it ever judges). It therefore locks nothing and never calls an FX
provider — it re-expresses the expense using the conversion a write path already
locked onto the row, and when nothing bridges the two currencies it treats the
threshold as **breached** rather than guessing. Details + the per-threshold
fail-closed table: `expense-management.md` § Threshold currency.

## Reporting (base) currency

Every org has one reporting currency. It is resolved by
`currency_conversion.resolve_reporting_currency(org.settings)` in this order:

1. `Organization.settings.reporting_currency` — the explicit setting.
2. `Organization.settings.payments.home_currency` — legacy field the payment
   path already reads (so existing orgs keep working with no settings change).
3. `Organization.settings.invoice_defaults.currency`.
4. `AP_REPORTING_CURRENCY_DEFAULT` (config default `USD`) — platform last resort.

The result is always an uppercase ISO 4217 code; a misconfigured org degrades to
the default rather than 500-ing a dashboard.

### Setting it

`PATCH /api/organization` with `{"settings": {"reporting_currency": "EUR"}}`
(admin-only, merged into existing settings).

## Materialized conversion on the invoice

Four nullable columns persist the conversion **at the time it is computed** so a
later market move never silently rewrites historical reporting numbers
(migration `0025_multicurrency`, tenant DBs only):

| Column | Type | Meaning |
|---|---|---|
| `invoices.reporting_currency` | `VARCHAR(3)` | Currency the row was converted into (snapshotted). |
| `invoices.reporting_amount` | `NUMERIC(15, 2)` | `amount` expressed in `reporting_currency`. |
| `invoices.reporting_fx_rate` | `NUMERIC(18, 8)` | Rate applied (invoice currency → reporting currency); `1` for same-currency. |
| `invoices.reporting_fx_locked_at` | `TIMESTAMPTZ` | When the rate was locked. |

These are written by `currency_conversion.materialize_reporting_amount(...)`,
invoked best-effort from `invoice_warnings.refresh_warnings` — the existing
chokepoint that already runs after every extraction and on every invoice
mutation. Behaviour:

- **Same currency** → rate `1`, no FX adapter call, `reporting_amount == amount`.
- **Idempotent** → an already-materialized row in the current reporting currency
  is left untouched; the locked rate is **never** re-fetched (historical
  stability). It re-locks only when the org's reporting currency changes or the
  row was never materialized.
- **Fail-soft** → an FX outage leaves the columns `NULL` and never blocks saving
  the invoice. Rollups treat a `NULL` reporting amount as "not yet converted"
  (see below).

Money is always `Decimal` / `Numeric` and quantizes to 2 dp (`ROUND_HALF_UP`);
the rate to 8 dp. Never `float`.

## Roll-up into the aggregates

`currency_conversion.rollup_to_reporting_currency(rows, reporting_currency=...)`
collapses a list of currency-tagged amount rows into one reporting total + a
per-currency breakdown, **without** any FX call:

- Rows with a rate-locked `reporting_amount` matching the current reporting
  currency use that locked figure (stable against market moves).
- Same-currency rows convert 1:1.
- Foreign rows with no usable lock fall back to face value and are counted in
  `unconverted_count`, so the UI can surface "N rows pending conversion" instead
  of silently mixing currencies.

Surfaced in:

- **`GET /api/dashboard`** → `reporting` block: `reporting_currency`,
  `total_amount`, `total_count`, `unconverted_count`, `by_currency[]`. The legacy
  `total_amount` (naive cross-currency `SUM`) is unchanged for back-compat;
  `reporting.total_amount` is the figure to trust for a multi-currency book.
- **`GET /api/analytics/cfo`** → `reporting_spend` block (same shape, scoped to
  the period window) plus `unrealized_fx` (below).

### Per-vendor rollups

Grouping spend **by vendor** (rather than one org-wide total) needs a second
helper: `currency_conversion.vendor_rollup_to_reporting_currency(rows,
reporting_currency=...)`. It groups currency-tagged rows by `vendor` first,
then runs `rollup_to_reporting_currency` **within each vendor's own rows**
before summing — a vendor that billed in both USD and EUR is converted
invoice-by-invoice and then added, never combined with a naive cross-currency
`SUM(amount)` (a vendor with a $1000 USD invoice and a €1000 invoice used to
report "$2000" — the face-value sum — instead of the correctly-converted
total). Returns a list of `VendorSpendEntry(vendor, amount, invoice_count,
currencies)` sorted by `amount` descending; `currencies` lists every distinct
original invoice currency that rolled into that vendor's total, so a
mixed-currency row stays auditable.

Surfaced everywhere a per-vendor spend total appears — all four now share this
one helper so they can never disagree with each other:

- **`GET /api/dashboard`** → `vendor_spend` (top 10)
- **`GET /api/analytics/cfo`** → `supplier_concentration` (top 50 feed the
  concentration math)
- **`GET /api/analytics/drill/spend_concentration`** → per-vendor drill-through
  rows (limited to `?limit=` **after** the per-vendor rollup, not at the SQL
  row level — limiting first would cut off a vendor's rows before they're
  fully summed)
- **`vendor_spend` CSV export / scheduled report** → `total_amount` +
  `currencies` columns (see `docs/analytics.md` § CSV export)

## Unrealized FX gain/loss

`currency_conversion.compute_unrealized_fx_gain_loss(open_invoices, ...)` marks
open (approved-but-unpaid) **foreign-currency** invoices to **today's** rate and
reports the difference vs the booked (rate-locked) reporting amount.

- One FX call per **distinct** foreign currency (not per row).
- Same sign convention as the realized figure: a **positive** number is a gain —
  the open liability is worth **less** in reporting terms than when booked.
- Same-currency invoices carry no FX exposure and are skipped.
- It becomes **realized** only when the invoice is paid, which is what
  `international_payments.compute_fx_gain_loss` measures — this is the
  open-position companion.

Open statuses considered: `approved`, `sending_to_erp`, `sent_to_erp`,
`posted_in_erp`, `payment_scheduled`.

Surfaced on `GET /api/analytics/cfo` → `unrealized_fx`:
`reporting_currency`, `total_unrealized_gain_loss`, `by_currency[]`
(`open_original_amount`, `booked_reporting_amount`, `current_reporting_amount`,
`unrealized_gain_loss`), and `available` (false when the FX lookup failed — the
CFO dashboard degrades rather than 500-ing).

## FX provider

Reuses the existing `services/fx_adapters/` registry (`mock` default,
`openexchangerates`) via `get_fx_adapter(org.settings.fx)` — no new provider.
Local dev / tests use the deterministic `mock` adapter, so multi-currency
rollups work with no cloud account (local-first).

## Config

| Env var | Default | Purpose |
|---|---|---|
| `AP_REPORTING_CURRENCY_DEFAULT` | `USD` | Platform last-resort reporting currency when an org sets none. |

## Tests

`tests/test_currency_conversion.py` (service: resolution, conversion, rate
locking/idempotency, rollup, per-vendor rollup, unrealized gain/loss),
`tests/test_dashboard_aggregations.py` (the wired-up dashboard reporting
rollup), `tests/test_analytics_rejected_exclusion.py` (realdb end-to-end
coverage for the per-vendor rollup agreeing across the dashboard and CFO
concentration tile for a vendor billing in more than one currency),
`tests/test_expense_currency.py` (the expense line/report layers), and
`tests/test_expense_policy.py` + `tests/test_expense_approval.py` (the
policy-threshold layer, incl. the fail-closed cases). All deterministic against
the mock FX adapter.
