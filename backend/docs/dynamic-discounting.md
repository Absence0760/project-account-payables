# Dynamic Discounting & Early-Payment Optimization

Goes beyond the static early-pay term on a `PaymentSchedule` ("2/10 net 30").
Suppliers (or the platform) offer **negotiable, time-boxed, sliding-scale**
early-payment discounts ("Pay in 5 days for 3% off"); the platform tracks each
offer through an accept / decline / capture lifecycle, ranks them by annualized
ROI against available cash, can auto-accept the high-ROI ones, and surfaces a
captured / missed / projected-savings dashboard. Optionally a supply-chain-
finance marketplace funds the early payment.

This is the feature behind roadmap **Priority 11 → Dynamic Discounting & Early
Payment Optimization**.

## Data model

`DiscountOffer` (`app/models/discount.py`, table `discount_offers`, migration
`0043_dynamic_discounting`, tenant-scoped + `EntityMixin`):

| Field | Notes |
|-------|-------|
| `scope` | `invoice` (one invoice) or `vendor` (a bulk negotiation across a vendor's open invoices) |
| `invoice_id` / `vendor_id` | the scoped target (one is set per `scope`) |
| `source` | `supplier` (proposed via portal/negotiation) \| `system` (derived from static terms) \| `financing` (funded by an SCF marketplace) |
| `status` | `offered` → `accepted` → `captured`, or `declined` / `expired` |
| `tiers` | JSONB sliding scale: `[{"days": 5, "percent": "3.00"}, …]`. **`percent` is a Decimal-string** (JSON has no Decimal) |
| `base_amount` | `Numeric(15,2)` — the amount the discount applies to (invoice amount, or summed open balance for a bulk offer) |
| `accepted_tier` / `accepted_at` / `accepted_by` | the chosen rung + who/when |
| `captured_amount` / `captured_at` | realized savings once `captured` |
| `financing_provider` | set when `source == financing` |
| `valid_from` / `valid_until` | offer window |

Money is `Numeric`/`Decimal` end-to-end; tier percents are Decimal-strings.
Migration is tenant-only (gated on `invoices`), idempotent
(`CREATE TABLE IF NOT EXISTS`), and fans out via
`scripts/migrate_all_tenants.py`. Fresh tenants get the table from
`tenant_provisioning._create_tenant_tables` (`create_all`).

## ROI economics (the shared primitive)

`app/services/discount_roi.py` is pure, deterministic, Decimal-exact. The
annualized return of taking a discount uses the textbook
cost-of-forgoing-discount formula:

```
APR = discount% / (100 - discount%) * 365 / days_accelerated
```

**`days_accelerated` is `net_due_date − discount_deadline`** — the days the
cash is accelerated by paying on the discount deadline instead of at the net
due date (a 2/10-net-30 discount → 20 days → ~37.2% APR). It is **not** the
discount period itself; measuring `today → deadline` overstates the APR.

`compute_roi(...)` returns savings, `annualized_return_pct`, `opportunity_cost`
(cost of parting with the discounted cash `days_accelerated` early at the org's
cost of capital), `net_benefit`, and `worthwhile` (APR > cost of capital). The
optimizer, the auto-capture sweep, and the per-invoice ROI endpoint all build
on this one module so the economics agree everywhere.

Cost of capital: per-org `Organization.settings.discounting.cost_of_capital_pct`
→ falls back to `FEOH_DISCOUNT_COST_OF_CAPITAL_PCT` (default 8.0).

## Services

| Module | Responsibility |
|--------|----------------|
| `discount_roi.py` | annualized-return primitive (above) |
| `discount_offers.py` | tier normalization/selection (`best_tier_for_date`, `select_tier` / `select_tier_for_date`), savings math, lifecycle mutators (`accept_offer` / `decline_offer` / `mark_captured` / `expire_if_past`), and `build_bulk_offer` (sum a vendor's open balances into a vendor-scoped offer). Pure — never commits |
| `discount_optimizer.py` | `optimize(opportunities, cash_budget, cost_of_capital_pct, today, reporting_currency=None)` — scores each opportunity, ranks by APR desc (tie-break savings, then id), and **greedily** selects the highest-yield `worthwhile` + still-capturable ones until the cash budget is exhausted (capture vs. cash preservation). `cash_budget=None` selects every worthwhile one. Pure. See [Currency](#currency--the-totals-are-sums) for `reporting_currency` |
| `discount_auto_trigger.py` | background sweep — auto-accepts open offers whose ROI clears `FEOH_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD`. Mirrors `contract_renewal` (per-tenant fan-out, fresh engine, one failure never halts the sweep). Also the sole place `expire_if_past` runs — flips an `offered` row whose `valid_until` has passed to `expired` before it's ever considered for auto-accept. **Money-path boundary: only flags `offered → accepted`; never creates a `Payment`/`PaymentRun`** — actual funding still flows through the CFO-gated payment run. The status guard is the dedupe |

### Currency — the totals are sums

`DiscountOffer.base_amount` carries its own `currency`, and every money figure
`optimize` returns (`total_savings_available` / `total_savings_selected` /
`total_outlay_selected`) is a **sum across offers**. Every caller then labels
that sum with a single currency: `/optimize` and `/dashboard` with
`_org_currency(org)`, the copilot's `optimize_discount_capture` with
`resolve_org_currency(...)`. A €1,000 offer beside a $1,000 offer was therefore
reported as "$1,960 committed, $40 saved".

`optimize` now takes `reporting_currency` — the currency those totals (and
`cash_budget`) are in. An opportunity in any other currency is flagged
`unconvertible` and:

- contributes to **none** of the three money totals, and is counted on
  `OptimizationResult.unconvertible_count` (surfaced as
  `OptimizerResponse.unconvertible_count`,
  `DiscountDashboard.unconvertible_offer_count`, and the copilot result's
  `unconvertible_count`); and
- is **never selected when a cash budget binds** — the budget is a
  reporting-currency figure, so a foreign outlay cannot be measured against it
  and must not consume it.

It IS still selectable under `cash_budget=None`: that decision involves no
cross-currency arithmetic at all (an APR is currency-free), and dropping a
genuinely worthwhile foreign discount from an unconstrained recommendation
would be a functional loss, not a safety gain. Nothing is converted here — a
rate fetched at read time would make a ranking move under the reader
(`../../docs/decisions.md` §18).

`services/cash_flow_plan.assemble_plan` honours the same flag: an
`unconvertible` offer is never re-timed onto the plan's cash curve (the curve
is in the reporting currency; its outlay is not) and is listed in
`unretimed_offer_ids` instead.

`reporting_currency=None` disables the guard, which is why every production
caller passes it and only the pure unit tests omit it.

**Tier window is measured from the offer, not from today.** Every call site that
resolves a tier — `best_tier_for_date` (best-tier-today) and
`select_tier_for_date` (a caller-named tier, e.g. the accept endpoints'
`tier_days`) — takes an optional `reference_date`, and it must come from
**`discount_offers.offer_reference_date(offer)`**. A tier `{"days": N}`'s real
deadline is `reference + N days`; omitting `reference_date` (or passing `None`)
silently measures every deadline from "today" instead, which makes every tier
look perpetually achievable regardless of how long the offer has been open —
the exact bug in issue #124.

`offer_reference_date` resolves **`valid_from`, else the offer's `created_at`
date** (UTC, matching `utils/dates.utc_today`). The second rung is what the
first fix was missing: every call site passed `offer.valid_from` directly, and
that column is nullable — `build_bulk_offer.as_offer_kwargs` has no `valid_from`
key **at all**, so *every bulk negotiation* was created with a NULL one, and
`DiscountOfferCreate.valid_from` defaults to `None` too. Those offers kept the
rolling deadline. An offer opened on Jan 1 with
`[{days: 5, percent: 3}, {days: 30, percent: 1}]` still selected the 3% rung in
August; on a 500,000 bulk offer that is a 15,000 deduction the supplier never
agreed to. `_tier_deadline` (the shared `pay_by` the router and the sweep both
render) goes through the same resolver. `None` is returned only for an
unpersisted offer carrying neither date, where "measure from today" is correct.

`select_tier` alone (no `_for_date` suffix) has **no date check at all**; only
use it when the caller has already verified the tier's window separately.

Guards: `tests/test_discount_offers.py` — the resolver's own cases, the
end-to-end aged-bulk-offer case, and an AST drift guard that fails when any call
site under `app/` resolves `reference_date` from anything other than
`offer_reference_date`.

### Supplier-financing adapters (`services/financing_adapters/`)

Same registry pattern as `fx_adapters` / `sanctions_adapters`. A financier pays
the supplier now (face value minus a fee); the buyer repays at the invoice's net
due date. Adapters `quote(...)` terms and `request_funding(...)`.

- `mock` — local-first default; deterministic fee (≈6% APR scaled by days-to-due),
  no network, no credential.
- `c2fo` — skeleton for a real SCF marketplace; **fails closed** without a key
  (no hardcoded secret). Selected per-org via `Organization.settings.financing.provider`.

**An unsupported provider name fails closed too.** `get_financing_adapter`
resolves an absent/empty `provider` to `mock` (the local-first default) but
raises `UnknownFinancingProviderError` for a NAMED provider it has no adapter
for. It used to substitute `mock` there as well — the last dispatcher in the
codebase still failing open, after `payment_adapters` / `erp_adapters` /
`fx_adapters` all closed (`../../docs/decisions.md` §29). `mock` is not an
inert stub: `request_funding` returns `funded=True` with a fabricated
`mock-fund-<hash>` id, i.e. it records a supplier as paid by a financier that
never saw the request, off a one-character typo in an admin-entered settings
value. Closed before the first production caller lands rather than after.

**Refusing is a return value, not an exception.** `base.FinancingAdapter.quote`
is explicit: an implementation returns an ineligible `FinancingQuote` rather
than raising when the provider declines — *a missing credential is the one case
that may fail closed (raise)*. The `c2fo` skeleton used to `raise
NotImplementedError` from both `quote` and `request_funding` even when fully
credentialled, which nothing catches today only because the family has no
production caller: the first one wired up would take a 500 from the one path
whose entire contract is that it answers "not eligible". It now returns
`eligible=False` / `funded=False` carrying the PII-free machine reason
`provider_not_implemented`, with every money field zeroed and no `funding_date`
claimed. `request_funding`'s `status` is `"unavailable"`, deliberately **not**
`"declined"` — no financier ever saw the request, and a caller must not record a
provider decision that never happened. No money moves, so a repeat call on the
same `idempotency_key` is trivially idempotent.

`test_connection` stays `False` on credentials alone, which is what makes the
soft refusal safe: the operator learns the integration cannot fund anything at
configuration time rather than on the first quote. That honest-probe rule is
enforced registry-wide across **every** adapter family by
`tests/test_adapter_contract_integrity.py` — an adapter whose method can never
do its job must be declared there, with the consequence for the caller written
down, and must report an unavailable probe.

## API (`/api/discounts`)

| Method + path | Roles | Purpose |
|---|---|---|
| `GET /offers` | all four | list (filters: `status` — `missed` = declined+expired — `scope`, `vendor_id`; paginated, entity-scoped) |
| `POST /offers` | admin, ap_manager | create an offer (invoice base_amount defaults from the invoice) |
| `GET /offers/{id}` | all four | detail (entity-scoped) |
| `POST /offers/{id}/accept` | admin, ap_manager, **cfo** | accept at a tier (`tier_days` or best tier today) |
| `POST /offers/{id}/decline` | admin, ap_manager, **cfo** | decline |
| `GET /invoices/{id}/roi` | all four | annualized ROI of paying the invoice early (open offer's best tier, else the static `PaymentSchedule` term) |
| `POST /optimize` | all four | rank open offers by ROI and select within an optional `{cash_budget}` |
| `POST /bulk-negotiate` | admin, ap_manager | one vendor-scoped offer across the vendor's open invoices |
| `GET /dashboard` | all four | captured / missed / capture-rate / open-offers / projected-savings rollup |

Every mutation writes an audit row (`discount_offer.created` / `.accepted` /
`.declined` / `.bulk_created`; the sweep writes `.auto_accepted`). Reads are
entity-scoped; lifecycle guards return `409`. Percent / ROI fields serialize as
JSON **numbers** (matching the frontend `number`-typed contract) while staying
`Decimal` in Python.

## Capture — from `accepted` to `captured`

`discount_offers.mark_captured` is the only code that sets
`captured_amount`/`captured_at` and transitions `accepted → captured`, but it
is a pure mutator — something has to notice "this payment settling this
invoice IS the discounted payoff" and call it. That caller is
`services/discount_capture.capture_offers_for_settled_payment`, wired into
every place a `Payment` reaches `completed` in `app/api/payments.py`:

- `_execute_single_payment`'s synchronous adapter-completed leg and its
  virtual-card leg (mock adapter, or any processor that confirms inline)
- the async webhook-driven completion in `payment_webhook` (the realistic
  path for a live ACH/wire processor, which sits `submitted`/`processing`
  until the provider calls back) — **unless the settlement itself didn't
  reconcile**: when `payment_settlement.verify_settlement` flags the
  processor's reported amount as diverging from what AP authorized, the
  capture is skipped and a payment-blocking `fraud_flag` opens instead. The
  payoff match below runs against `Payment.amount` — our authorized figure,
  which the rail has just contradicted — so capturing there would mark
  savings realized on a number in dispute. See
  [payments.md](payments.md) § Settlement-amount verification.

Both call the shared `_capture_discount_offers` helper, which resolves any
still-`accepted` **invoice-scoped** `DiscountOffer` on the settled invoice and
first checks the offer's `currency` against the invoice's own `currency`
(case-insensitive) — `POST /api/discounts/offers` lets the caller set an
explicit `currency` independent of the invoice (falling back to
`invoice.currency` only when omitted, `api/discounts.py::create_offer`), and
`Payment.amount` is always denominated in the invoice's currency, so a
currency-mismatched offer's `base_amount` (still defaulted from the invoice's
bare number) could otherwise numerically coincide with an unrelated payment
and be falsely captured. Only once currency matches does it check whether
`Payment.amount` **exactly** equals that offer's accepted tier's discounted
payoff (`base_amount - discount_savings(base_amount, accepted_tier)`, both
cent-quantized the same way). A match calls `mark_captured` and writes a
`discount_offer.captured` audit row (`actor_id` is the executing user on the
synchronous legs, `None` — a system/processor event — on the webhook leg,
matching the existing `payment.completed` webhook audit convention). A
non-matching currency or amount leaves the offer `accepted` rather than
guessing — a false capture would misreport savings exactly like the original
missing-caller bug, just inverted.

**Vendor-scoped bulk offers are intentionally out of scope here.** A bulk
offer's `base_amount` is the summed open balance across several invoices, so
no single invoice's payment can be proven to BE that offer's settlement;
those are left `accepted` for a future reconciliation pass rather than
attributed to whichever invoice happened to pay first.

**How AP actually pays the discounted amount today**: nothing in the payment
run path automatically nets a `DiscountOffer`'s discount off `Payment.amount`
— `create_payment_run_for_invoices` only nets *applied credit memos*. Paying
at the discounted payoff means recording a credit memo for the discount
amount (or otherwise adjusting the invoice) before scheduling the payment, so
the existing credit-memo-netting math lands `Payment.amount` on the
discounted figure. See `tests/test_discount_capture.py` for the exact flow.

**Idempotent**: `capture_offers_for_settled_payment` only queries offers
currently `accepted`, so a retried settlement or a reconciliation re-run over
the same invoice/payment finds nothing left to capture — never double-counts,
never raises on an already-`captured` offer. `mark_captured`'s own status
guard is a second backstop against a genuine race between two settlement
paths. **Best-effort**: the whole capture attempt (including resolving the
invoice on the webhook leg) runs inside a try/except in
`_capture_discount_offers` — a failure here is logged (exception class only)
and swallowed, never the reason a payment that DID settle fails to record
that it settled, or the reason a webhook delivery 5xxs and gets needlessly
retried.

**`GET /api/dashboard`'s `discount_capture` KPI is a different feature and is
NOT affected by this.** It rolls up `PaymentSchedule.discount_percent` /
`discount_date` — the *static* "2/10 net 30" term captured at invoice
creation (see the module docstring) — via
`services/analytics.compute_discount_capture`, entirely independent of
`DiscountOffer`. Only `GET /api/discounts/dashboard`'s `captured_amount` /
`captured_count` (summed straight off `DiscountOffer.captured_amount`) reads
the fix in this section.

### Supplier portal (`/api/portal/discount-offers`)

The same offers are surfaced to the **vendor** so a supplier can accept an
early-payment discount the AP team extends to them. The portal routes
(`api/portal.py`, vendor-scoped via `get_current_vendor_user`) reuse the **same
pure `services/discount_offers.py` primitives** — `accept_offer` / `decline_offer`
/ `best_tier_for_date` / `discount_savings` — so the Decimal math and lifecycle
are never duplicated.

| Method + path | Purpose |
|---|---|
| `GET /portal/discount-offers` | offers scoped to the caller's own `vendor_id` **or** their own invoices; per-tier savings + best capturable tier today; `?status=` filter |
| `POST /portal/discount-offers/{id}/accept` | accept at a tier (`tier_days` or best today); flips `offered → accepted` only — **never moves money** (CFO-gated payment run still funds it); re-accept is a `409`; foreign/unknown id `404` |
| `POST /portal/discount-offers/{id}/decline` | decline; `409` if no longer `offered` |

A vendor can never see another vendor's offers (cross-vendor / unknown id → 404,
never 403). Audit rows are `discount_offer.accepted_by_vendor` /
`.declined_by_vendor` — PII-free, `actor_id=None` (a `VendorUser` is not a
control-plane user). No migration — the `DiscountOffer` table already exists.
See `supplier-portal.md` § Early-payment discount offers.

## Config (`FEOH_` env vars)

| Var | Default | Purpose |
|-----|---------|---------|
| `FEOH_DISCOUNT_OPTIMIZATION_ENABLED` | `false` | master switch for the auto-capture background sweep — keep `false` in local dev, flip on in deployed envs |
| `FEOH_DISCOUNT_OPTIMIZATION_INTERVAL_SECONDS` | `3600` | sweep interval |
| `FEOH_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD` | `12.0` | annualized return (APR %) an offer must clear for the sweep to auto-accept it |
| `FEOH_DISCOUNT_COST_OF_CAPITAL_PCT` | `8.0` | platform-default annual cost of capital; per-org override `settings.discounting.cost_of_capital_pct` |

The ROI calculator, offer lifecycle, optimizer, and dashboard run
unconditionally; only the *auto-accept sweep* is gated (and it never moves
money). Local-first: the financing adapter defaults to `mock`, so `pnpm dev`
needs no credential.

## Frontend

`/discounts` (`routes/discounts/+page.svelte`, gated to admin/ap_manager/cfo):
KPI cards (captured / missed / capture rate / projected savings / open offers),
a status `FilterChips` filter, an offers `DataTable` (tiers via the new
`ui/DiscountTierBar.svelte`, accept-tier `Modal`, decline action), and an
"Early-payment optimizer" panel posting to `/optimize`. API wrappers in
`lib/api/discounts.ts`, types in `lib/types/discounts.ts`.

**Supplier portal:** `/portal/discount-offers`
(`routes/portal/discount-offers/+page.svelte`) lists the vendor's own offers with
per-tier savings + the best capturable tier today and lets them Accept (tier
picker + live savings preview) or Decline. API helpers + types live in
`lib/portalApi.ts` (`listPortalDiscountOffers` / `acceptPortalDiscountOffer` /
`declinePortalDiscountOffer`); reachable from the **Discounts** link in the
portal nav.

## Tests

- `tests/test_discount_roi.py` (foundation), `test_discount_offers.py`,
  `test_discount_optimizer.py`, `test_financing_adapters.py` — pure unit.
- `test_discount_auto_trigger.py` — sweep fan-out + real-DB mutation
  (worthwhile→accept, threshold gate, money-path boundary, idempotency, audit).
- `test_discounts_api.py` — router end-to-end (lifecycle, ROI, optimizer, bulk,
  dashboard, RBAC, tenant isolation).
- `test_portal_discount_offers.py` — supplier-portal list + accept/decline
  (real-DB): vendor scoping (own vendor + own invoices, never another vendor),
  per-tier savings, accept flips status without creating a `Payment`/`PaymentRun`,
  double-accept 409, foreign/unknown offer 404, auth-required.
- `test_discount_capture.py` — the capture wiring (real-DB, end to end through
  `POST /api/payments/runs` + `.../execute`): a payment settled at the exact
  discounted payoff captures the offer + updates the dashboard; a payment
  settled at the full amount does NOT falsely capture; a currency-mismatched
  offer (explicit `currency` diverging from its own invoice) does NOT falsely
  capture even when the numbers numerically coincide; repeat calls to
  `capture_offers_for_settled_payment` are idempotent (no double-count, no
  error on an already-`captured` offer).
- `frontend/tests-e2e/discounts/money-path.spec.ts` — live-stack e2e asserting
  the exact savings/ROI/APR Decimal values, best-vs-explicit tier selection,
  accept idempotency (double-accept is a safe 409, no double-count), the
  accept-never-moves-money boundary (no `Payment` row appears), the
  optimizer's APR ranking + cash-budget binding (entity-scoped for
  determinism), accept/decline RBAC (both also cfo), and cross-tenant
  isolation of offers.

## Elapsed discount windows are not "eligible"

Four places price the same economics, and they must not disagree about which
discounts are still on the table:

| Owner | Rule |
|---|---|
| `discount_offers._tier_achievable` | a tier is achievable only while its window is open |
| `discount_optimizer.optimize` | `capturable = today <= opp.pay_by` |
| `analytics.apply_payment_timing_scenario` (`early`) | re-times onto `discount_date` only while `discount_date >= today` |
| `analytics.bucket_outflows` (`discount_eligible_amount`) | same rule — **this was the outlier** |

The commitment rows the forecast consumes are bounded on their DUE date only
(`api/analytics._commitment_rows`), so an in-horizon invoice on 2/10-net-60
terms routinely arrives carrying a `discount_date` that passed weeks ago.
Counting it made `/analytics/cashflow_forecast` report a saving nobody can
still take — and the copilot narrates off that figure. `bucket_outflows` now
takes `today` (defaulting to the UTC date, mirroring
`apply_payment_timing_scenario`) and gates on it. The row's outflow is
unchanged; only its *eligibility* is.

## `create_offer` resolves the invoice inside the caller's entity scope

An invoice-scoped offer looks its invoice up through `apply_entity_scope`, the
same way `payment_runs.create_payment_run_for_invoices` and the credit-memo
path do. The offer is STAMPED with the caller's write entity, so without the
scope filter an operator with subsidiary A selected could raise an offer under
A against subsidiary B's invoice — visible in A's queue while pricing B's
payable. Advisory data, never money, but the sibling money path was fixed for
exactly this shape. An out-of-scope id is the same opaque 404 as a missing one.

## Currency integrity

Every money figure on the discounting surfaces is denominated in the org's
**reporting currency**, resolved by the one canonical
`currency_conversion.resolve_reporting_currency` (explicit
`settings.reporting_currency` → `payments.home_currency` →
`invoice_defaults.currency` → `FEOH_REPORTING_CURRENCY_DEFAULT`).
`api/discounts._org_currency` is a thin named wrapper over it. It previously
read the first key alone and fell back to a hardcoded `"USD"`, so an org that
set a home currency but no explicit reporting currency had every discount
figure stamped USD, and a non-USD deployment's platform default was ignored.

`GET /api/discounts/dashboard` reports **one** currency code, so every money
field in it aggregates only rows denominated in that code:

| Field | Population |
|---|---|
| `captured_amount` / `captured_count` | captured offers in the reporting currency |
| `missed_amount` / `missed_count` | declined + expired offers in the reporting currency |
| `projected_savings` | open offers in the reporting currency (via the optimizer) |
| `excluded_captured_count` / `excluded_missed_count` / `unconvertible_offer_count` | how many rows each figure left out |

`captured_amount` and `missed_amount` were previously bare cross-currency
`SUM`s stamped with the reporting currency, while `projected_savings` in the
same response was already filtered — one response carried one currency-correct
figure and two that were not. A cross-currency sum under a single code is not
an approximation of the truth but a different quantity, and it moves silently
whenever the currency mix does.

The figures are **filtered rather than converted** on purpose: these are
historical realised amounts, and fetching an FX rate during a dashboard read
would make the number non-deterministic (`services/cashflow` refuses the same
trade for the same reason, see `docs/cash-flow-copilot.md` §12). The excluded
counts ride along so a partial figure is visibly partial rather than quietly
short. `capture_rate_pct` is computed over the same reporting-currency
population as the counts beside it, so every field in the response describes
one set.

Two conventions worth knowing before reading the response:

- **`open_offer_count` is deliberately whole-set** while the three money figures
  are reporting-currency-only. It answers "how much is on the table to work
  through", which is a queue depth rather than an amount, and dropping foreign
  offers from it would hide work that still needs a decision.
  `unconvertible_offer_count` is what reconciles it against
  `projected_savings`.
- **Money crosses the wire as a JSON number, not a decimal string.** These
  schemas use `app/schemas/money.py::MoneyAmount`, which serialises `Decimal`
  to a number at write time (see that module for the >2^53-cents caveat). The
  exactness invariant holds where it matters — `Decimal` in Python and
  `Numeric` on the column, never a float — so `missed_amount`, the one figure
  accumulated in Python rather than by a Postgres `SUM`, still reports `0.30`
  over thirty one-cent misses where a float loop lands on
  `0.3000000000000001`.

### The auto-capture sweep never overwrites a human decision

`discount_auto_trigger` selects its candidates **unlocked**, then does per-row
async work (cost-of-capital resolution, due-date lookup, ROI) before deciding.
A supplier or an AP user can decline or accept an offer inside that window.

The sweep previously mutated the stale ORM object and issued an unconditional
`UPDATE ... SET status` at its single end-of-loop commit, so a *committed*
decline was silently overwritten: the offer came back `accepted`, and an
append-only `discount_offer.auto_accepted` audit row asserted the sweep had
found it open. Because the trail is append-only, that false entry could not be
corrected afterwards.

Each status write now re-reads its row under `FOR UPDATE` with a
`status = 'offered'` predicate (`_claim_if_still_offered`) immediately before
mutating; a row someone else has moved returns `None` and is skipped, with no
audit row. `populate_existing` is required — without it the second SELECT
returns the stale identity-mapped object and re-checks nothing.

The lock is taken at the point of mutation rather than on the candidate scan on
purpose: holding `FOR UPDATE` across the whole loop would keep a growing lock
set open across unrelated awaits, the pattern `payment_reconciler` is already
flagged for in `docs/followups.md`. Expiry (`expire_if_past`) takes the same
claim — it is a status write too.
