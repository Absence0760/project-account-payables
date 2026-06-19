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
→ falls back to `AP_DISCOUNT_COST_OF_CAPITAL_PCT` (default 8.0).

## Services

| Module | Responsibility |
|--------|----------------|
| `discount_roi.py` | annualized-return primitive (above) |
| `discount_offers.py` | tier normalization/selection (`best_tier_for_date`, `select_tier`), savings math, lifecycle mutators (`accept_offer` / `decline_offer` / `mark_captured` / `expire_if_past`), and `build_bulk_offer` (sum a vendor's open balances into a vendor-scoped offer). Pure — never commits |
| `discount_optimizer.py` | `optimize(opportunities, cash_budget, cost_of_capital_pct, today)` — scores each opportunity, ranks by APR desc (tie-break savings, then id), and **greedily** selects the highest-yield `worthwhile` + still-capturable ones until the cash budget is exhausted (capture vs. cash preservation). `cash_budget=None` selects every worthwhile one. Pure |
| `discount_auto_trigger.py` | background sweep — auto-accepts open offers whose ROI clears `AP_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD`. Mirrors `contract_renewal` (per-tenant fan-out, fresh engine, one failure never halts the sweep). **Money-path boundary: only flags `offered → accepted`; never creates a `Payment`/`PaymentRun`** — actual funding still flows through the CFO-gated payment run. The status guard is the dedupe |

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

## Config (`AP_` env vars)

| Var | Default | Purpose |
|-----|---------|---------|
| `AP_DISCOUNT_OPTIMIZATION_ENABLED` | `false` | master switch for the auto-capture background sweep — keep `false` in local dev, flip on in deployed envs |
| `AP_DISCOUNT_OPTIMIZATION_INTERVAL_SECONDS` | `3600` | sweep interval |
| `AP_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD` | `12.0` | annualized return (APR %) an offer must clear for the sweep to auto-accept it |
| `AP_DISCOUNT_COST_OF_CAPITAL_PCT` | `8.0` | platform-default annual cost of capital; per-org override `settings.discounting.cost_of_capital_pct` |

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
- `frontend/tests-e2e/discounts/money-path.spec.ts` — live-stack e2e asserting
  the exact savings/ROI/APR Decimal values, best-vs-explicit tier selection,
  accept idempotency (double-accept is a safe 409, no double-count), the
  accept-never-moves-money boundary (no `Payment` row appears), the
  optimizer's APR ranking + cash-budget binding (entity-scoped for
  determinism), accept/decline RBAC (accept also cfo), and cross-tenant
  isolation of offers.
