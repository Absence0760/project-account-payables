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
| `discount_optimizer.py` | `optimize(opportunities, cash_budget, cost_of_capital_pct, today)` — scores each opportunity, ranks by APR desc (tie-break savings, then id), and **greedily** selects the highest-yield `worthwhile` + still-capturable ones until the cash budget is exhausted (capture vs. cash preservation). `cash_budget=None` selects every worthwhile one. Pure |
| `discount_auto_trigger.py` | background sweep — auto-accepts open offers whose ROI clears `FEOH_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD`. Mirrors `contract_renewal` (per-tenant fan-out, fresh engine, one failure never halts the sweep). Also the sole place `expire_if_past` runs — flips an `offered` row whose `valid_until` has passed to `expired` before it's ever considered for auto-accept. **Money-path boundary: only flags `offered → accepted`; never creates a `Payment`/`PaymentRun`** — actual funding still flows through the CFO-gated payment run. The status guard is the dedupe |

**Tier window is measured from `valid_from`, not from today.** Every call site that resolves a tier — `best_tier_for_date` (best-tier-today) and `select_tier_for_date` (a caller-named tier, e.g. the accept endpoints' `tier_days`) — takes an optional `reference_date` that should be the offer's `valid_from` (when it was actually extended). A tier `{"days": N}`'s real deadline is `valid_from + N days`; omitting `reference_date` (or passing `None`) silently measures every deadline from "today" instead, which makes every tier look perpetually achievable regardless of how long the offer has been open — the exact bug in issue #124. `select_tier` alone (no `_for_date` suffix) has **no date check at all**; only use it when the caller has already verified the tier's window separately.

### Supplier-financing adapters (`services/financing_adapters/`)

Same registry pattern as `fx_adapters` / `sanctions_adapters`. A financier pays
the supplier now (face value minus a fee); the buyer repays at the invoice's net
due date. Adapters `quote(...)` terms and `request_funding(...)`.

- `mock` — local-first default; deterministic fee (≈6% APR scaled by days-to-due),
  no network, no credential.
- `c2fo` — skeleton for a real SCF marketplace; **fails closed** without a key
  (no hardcoded secret). Selected per-org via `Organization.settings.financing.provider`.

## API (`/api/discounts`)

| Method + path | Roles | Purpose |
|---|---|---|
| `GET /offers` | all four | list (filters: `status` — `missed` = declined+expired — `scope`, `vendor_id`; paginated, entity-scoped) |
| `POST /offers` | admin, ap_manager | create an offer (invoice base_amount defaults from the invoice) |
| `GET /offers/{id}` | all four | detail (entity-scoped) |
| `POST /offers/{id}/accept` | admin, ap_manager, **cfo** | accept at a tier (`tier_days` or best tier today) |
| `POST /offers/{id}/decline` | admin, ap_manager | decline |
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
  determinism), accept/decline RBAC (accept also cfo), and cross-tenant
  isolation of offers.
