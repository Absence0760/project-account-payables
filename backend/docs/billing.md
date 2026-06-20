# Platform Billing & Metering

How the platform bills its own customers (the orgs/tenants) — plans,
subscriptions, usage metering, entitlement gating, and the pluggable billing
provider. This is the AP platform's *own* revenue plumbing, distinct from the
accounts-payable money path the app manages for customers.

> **Status.** Shipped: the control-plane plan/subscription model, a usage rollup
> off the existing meters, a `mock`-default billing adapter family, an
> entitlement gating helper wired onto the public `/api/v1` surface, a customer
> read endpoint + UI, **the live `stripe_billing` create/get-subscription +
> report-usage API calls, the inbound HMAC-verified + deduped webhook route, and
> the dunning / past-due automation sweep.** **Deferred to later slices:**
> proration math, plan-change / payment-method / invoice-list endpoints, and a
> per-org Stripe customer/price provisioning flow (the create call expects the
> resolved Stripe `customer`/`price` ids in adapter config).

## Where it lives (control plane)

Billing is a property of the **customer account**, so — like `Organization`,
`User`, `ExtractionUsage`, `CardRebate`, and `ApiKey` — it lives in the
**control-plane** DB (`account_payables`) keyed by `organization_id`. It never
fans out to per-tenant DBs. The two tables are in `CONTROL_TABLES`
(`services/tenant_provisioning.py`), guarded by the coverage test in
`tests/test_tenant_provisioning.py`.

### Models (`app/models/billing.py`)

| Model | Purpose |
|-------|---------|
| `Plan` | A sellable tier. `code` (stable machine id, unique) + `name`, `monthly_price` (`Numeric(12,2)`), `currency`, `seat_component` (JSONB), `usage_components` (JSONB), `entitlements` (JSONB, e.g. `{"public_api": true, "max_seats": 25}`), `trial_days`, `is_active`. |
| `Subscription` | Binds one org to one plan. `organization_id` FK, `plan_id` FK, `status` (`trialing`/`active`/`past_due`/`canceled`), `current_period_start`/`_end`, `trial_end`, nullable `external_subscription_id` (the live provider's id). |

**Money invariant:** `monthly_price` is `Numeric`; per-seat / usage component
prices are stored as decimal **strings** in JSONB and parsed back to `Decimal` —
never float, anywhere.

**One live subscription per org** is enforced by a partial unique index
`uq_subscription_one_live_per_org ON subscriptions (organization_id) WHERE
status <> 'canceled'` (a canceled row is kept for history). Migration
**`0056_platform_billing`** (control-plane-gated + idempotent DDL, mirrors
`0055_api_keys`).

## Usage rollup (`services/billing/usage_rollup.py`)

`rollup_usage(db, organization_id=…, period="YYYY-MM") -> UsageRollup` aggregates
the existing control-plane meters into billable counters. Pure read, no mutation,
`Decimal`-exact (`card_rebate_total` sums the `Numeric` `card_rebates.amount` via
`COALESCE(..., 0.00)` so an empty month yields `Decimal('0.00')`, never `None`).

| Meter | Source |
|-------|--------|
| `extractions` | count of `extraction_usage` rows in the period |
| `extractions_platform` | the `program_type='platform'` (billable) subset |
| `card_rebate_total` | sum of `card_rebates.amount` (informational this slice) |

`UsageRollup.as_meters()` serializes to a `dict[str, str]` (money + counts as
exact strings) for the API/adapter payload.

## Billing adapters (`services/billing_adapters/`)

Same registry/decorator/dispatcher pattern as the email / PEPPOL / QMS families.

```python
@register_billing_adapter("my_provider")
class MyAdapter(BillingAdapter):
    async def create_subscription(self, request: CreateSubscriptionRequest) -> ProviderSubscription: ...
    async def get_subscription(self, external_subscription_id: str) -> ProviderSubscription: ...
    async def report_usage(self, report: UsageReport) -> None: ...
    def parse_webhook(self, headers: dict, body: bytes) -> BillingWebhookEvent | None: ...
    async def test_connection(self) -> bool: ...
```

| Adapter | Notes |
|---------|-------|
| `mock` (**default**) | In-process, deterministic, no network/credential. Synthetic `mock_sub_<org>` id; `report_usage` is a no-op; `parse_webhook` reads a dev JSON envelope. Local-first. |
| `stripe_billing` | Live key via sops, **fails closed** (`BillingNotConfigured`) without `AP_BILLING_STRIPE_API_KEY`. `create_subscription` / `get_subscription` / `report_usage` are now **implemented** against the Stripe REST API via `httpx` (key as HTTP-Basic username, form-encoded bodies; subscription create sends an `Idempotency-Key` header so a retried create can't double-subscribe; `report_usage` POSTs one Billing Meter Event per meter with the quantity as an exact decimal **string**, never float). `create_subscription` expects the resolved Stripe `customer`/`price` ids in adapter config (per-org provisioning of those is a later slice → `BillingNotConfigured` if absent). A non-2xx raises a PII-free `BillingProviderError` (status + op only, never the response body). `parse_webhook` verifies the `Stripe-Signature` HMAC over the raw body via `services/webhook_security.verify_hmac_sha256` and maps Stripe statuses → our four-state lifecycle. |

`get_billing_adapter(provider=None)` resolves: explicit arg → `AP_BILLING_PROVIDER`
→ `mock`. An unknown name falls back to `mock` (a bad config can't break read
paths). Per-org override: `Organization.settings.billing.provider`. The
dispatcher injects the process-level Stripe key / webhook secret / API base from
config.

## Inbound webhook route (`app/api/billing_webhook.py`)

`POST /api/billing/webhook/{provider}` — **PUBLIC, no JWT** (the provider HMAC is
the gate; the route is in `NO_AUTH_REQUIRED`). The billing provider POSTs here
when a subscription's lifecycle changes (trial ends, payment fails → `past_due`,
dunning exhausts → `canceled`).

Billing is **control-plane** (keyed by org), so — unlike the payment webhook,
which carries the tenant slug in its URL path — this route resolves the affected
`Subscription` by the provider's subscription id **carried in the event itself**
(`external_subscription_id`, persisted on the row at create time). The provider
id is the tenant boundary here.

Pipeline (mirrors the PEPPOL-inbound webhook, honouring invariant #9):

1. **Master switch** `AP_BILLING_WEBHOOK_ENABLED` — OFF in local dev (no outbound
   billing integration), flipped ON in deployed envs. Off → silent 204.
2. **Body-size cap** (512 KiB) checked on the declared `Content-Length` *and* the
   actual read (memory-exhaustion guard on a public route).
3. **Provider match** — the `{provider}` path segment must equal the configured
   `AP_BILLING_PROVIDER`, else silent 204 (don't accept a different provider's
   unverifiable payload).
4. **HMAC verify + normalize** inside the adapter's `parse_webhook` (fail-closed:
   no secret / bad signature / unparseable → `None` → silent 204).
5. **Dedupe by `event_id`** via `webhook_security.is_event_already_processed`
   (Redis `SET NX EX`, keyed `billing:<provider>:<event_id>`). A provider
   redelivery within the window short-circuits — the lifecycle effect ran exactly
   once.
6. **Apply** the idempotent transition via `services/billing/webhook_processing.apply_billing_event`.

Every rejection path returns **204 silently** with a PII-free reason-code log —
a distinct 4xx would enumerate which providers / secrets / subscription ids are
accepted.

### Status transition (`services/billing/webhook_processing.py`)

`apply_billing_event(control_db, event=…)`:

- drops events with no `external_subscription_id` (account-level, no lifecycle
  effect) or no mapped lifecycle status;
- resolves the `Subscription` by `external_subscription_id` — unknown → drop
  (no enumeration);
- **idempotent**: if the target status already equals the current status it's a
  no-op and writes **no** audit row (mirrors `transition_invoice`'s no-op rule);
- otherwise sets the new status, commits, and writes an **append-only**
  `billing.subscription_<status>` audit row via the shared `dispatch_auth_audit`
  (PII-free: org + from/to status + event id/type; `actor_id=None` — provider-
  driven, no human actor). The audit row lands in the tenant `audit_log` (the
  control plane has none).

The four lifecycle states are `trialing → active → past_due → canceled`; the
Stripe adapter's `_STATUS_MAP` collapses `incomplete`/`unpaid` → `past_due` and
`incomplete_expired` → `canceled`.

## Dunning / past-due automation (`services/billing/dunning_sweep.py`)

The provider's own retry schedule (Stripe Smart Retries) normally drives a
failing subscription `active → past_due → canceled` and each hop arrives via the
webhook above. The dunning sweep is the **backstop** for when a terminal provider
webhook never arrives: a subscription that has sat `past_due` longer than
`AP_BILLING_DUNNING_GRACE_DAYS` (measured from `current_period_end`; a row with no
period end is overdue by default) is flagged `canceled` with an append-only
`billing.subscription_canceled` audit row.

**Money-path boundary:** the sweep ONLY changes a `Subscription` status — it
never charges, refunds, or creates any payment-side row. (A canceled subscription
grants nothing via `get_entitlements`; that down-grade is a read consequence, not
a money op.) **Control-plane only** — `Subscription` lives in the control DB, so
one query, no per-tenant fan-out. **Idempotent** — only `past_due` rows are
touched and canceling moves a row out of `past_due`. Long-lived asyncio task in
`main.lifespan`, OFF by default (`AP_BILLING_DUNNING_ENABLED`).

## Entitlement gating (`services/billing/entitlements.py` + `api/deps.py`)

`get_entitlements(db, org_id)` returns the `entitlements` JSON of the plan behind
the org's **live** subscription, or `{}` when there is none (fail-closed — a
feature is granted only when a plan explicitly includes it).

Two composable FastAPI dependencies in `api/deps.py`, both **on top of** auth —
they never replace `require_roles` / `require_api_scope`:

| Dependency | Surface | On miss |
|------------|---------|---------|
| `require_entitlement("feature")` | JWT (SPA) routes | **402 Payment Required** |
| `require_api_entitlement("feature")` | API-key `/api/v1` routes | **402 Payment Required** |

402 (upgrade your plan) is deliberately distinct from a 403 role denial.

**Wired demonstration:** the public `/api/v1/invoices` read routes now require
`require_api_entitlement("public_api")` alongside `require_api_scope("read")` —
the public API is a paid-plan feature. An org with no plan, or a plan whose
`entitlements.public_api` is falsy, gets a 402; a plan with `public_api: true`
passes.

## Customer endpoint (`app/api/billing.py`)

`GET /api/billing/subscription` (JWT + `require_roles(admin, cfo)`) returns the
tenant's current plan + subscription status + usage-to-date for the current
period:

```json
{
  "provider": "mock",
  "plan": {"code": "growth", "name": "Growth", "monthly_price": "49.00",
           "currency": "USD", "entitlements": {"public_api": true}, "trial_days": 14},
  "subscription": {"status": "active", "current_period_start": "...",
                   "current_period_end": "...", "trial_end": null,
                   "externally_managed": false},
  "period": "2026-06",
  "usage": {"extractions": "12", "extractions_platform": "10", "card_rebate_total": "0.00"}
}
```

`plan`/`subscription` are `null` when the org has no live subscription. Money is
an exact decimal **string** (this is a billing surface — exactness is the point).
Plan-change, payment-method, and invoice-list endpoints are later slices.

## Customer-facing UI (`frontend/src/routes/billing/`)

The read/display surface for the endpoint above. Route `/billing`, mounted as
the **Subscription** sub-tab of the existing **Billing** nav group
(`$lib/nav.ts`, `labelKey: 'nav.platformBilling'`), admin/cfo-gated to match the
backend (`require_roles(admin, cfo)`); a clerk/manager is redirected to the
dashboard and never sees the tab.

- `+page.svelte` consumes `GET /api/billing/subscription` via
  `$lib/api/billing.ts::getBillingSubscription` (types in
  `$lib/types/billing.ts`) — the shared `api` client adds the JWT + tenant
  header.
- Renders: the current plan (tier name, monthly price via `<Money>` so the
  exact decimal string is formatted, not re-computed), a `SubscriptionBadge`
  status pill (`trialing`/`active`/`past_due`/`canceled`), the billing-period
  window + trial-end (when trialing), the granted entitlement flags, and the
  usage-to-date meters (`KpiCard`s for extractions / billable extractions /
  card rebates).
- **States:** loading, error-with-retry, and a friendly **empty state** (no
  live subscription → "No active subscription" + a contact-sales link, usage
  meters still shown).
- The live-Stripe **plan-change / payment-method** actions are a later backend
  slice; they're surfaced as **disabled** "coming soon" buttons + a "contact
  us" link so the surface reads complete without implying an unwired action.
- `SubscriptionBadge.svelte` (`$lib/components/ui/`) is a new shared status pill
  for the four subscription states (WCAG-1.4.3-calibrated tones, matching
  `StatusBadge`).
- e2e: `frontend/tests-e2e/billing/billing.spec.ts` — header + empty state +
  usage meters, a seeded-Plan/Subscription happy path (plan name, exact `$49.00`
  price, Active badge, entitlement flag), the Subscription section tab
  visible/active for admin, and clerk RBAC (redirect + no tab + API 403). The
  billing rows live in the control plane, so the spec seeds them via
  control-plane psql and tears down in `finally`.

## Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_BILLING_PROVIDER` | `mock` | Billing adapter — `mock` (local-first default) \| `stripe_billing`. Per-org override `Organization.settings.billing.provider`. |
| `AP_BILLING_STRIPE_API_KEY` | (empty) | Live Stripe Billing secret key — **no hardcoded fallback**; sops in deployed. The `stripe_billing` adapter fails closed without it. |
| `AP_BILLING_STRIPE_WEBHOOK_SECRET` | (empty) | HMAC secret for Stripe webhook signature verification — no fallback; sops in deployed. |
| `AP_BILLING_STRIPE_API_BASE` | `https://api.stripe.com` | Stripe REST API base URL — overridable so a sandbox / test can point the adapter elsewhere. The adapter still fails closed without an API key regardless. |
| `AP_BILLING_WEBHOOK_ENABLED` | `false` | Master switch for the inbound billing webhook route (`POST /api/billing/webhook/{provider}`). OFF in local dev (no outbound billing integration); flip ON in deployed envs. The route is HMAC-gated regardless; off → silent 204. |
| `AP_BILLING_DUNNING_ENABLED` | `false` | Master switch for the dunning / past-due automation sweep. OFF by default; flip ON in deployed envs. The sweep only cancels subscriptions overdue past the grace window — it NEVER moves money. |
| `AP_BILLING_DUNNING_INTERVAL_SECONDS` | `3600` | Dunning sweep tick interval. |
| `AP_BILLING_DUNNING_GRACE_DAYS` | `14` | Grace window (days from `current_period_end`) a subscription may sit `past_due` before the dunning sweep cancels it. |

## Tests

`backend/tests/test_billing.py` — adapter default + fallback, mock determinism,
Stripe fail-closed + webhook HMAC verify/reject, entitlement allow/deny, rollup
Decimal-exactness, the subscription endpoint (plan + status + usage, admin/cfo
gating, null-plan case), and the `/api/v1` plan-gate (402 without `public_api`,
200 with). The control-tables coverage test in
`tests/test_tenant_provisioning.py` includes `plans` + `subscriptions`.

`backend/tests/test_billing_webhook.py` — the live-Stripe adapter calls
(create/get-subscription status mapping + idempotency-key header,
customer/price-required fail-closed, report-usage one-event-per-meter with exact
decimal-string values, empty-meter no-op, PII-free provider error) against a
mocked `httpx` transport (no network); the inbound webhook route end-to-end on
the real-Postgres harness (signed event drives the transition + audit row, bad
signature → 204 no change, dedupe-by-event-id → one effect, idempotent same-
status → no audit, unknown subscription → 204, disabled switch → 204, provider
mismatch → 204); and the dunning sweep (cancels overdue `past_due` + audit +
idempotent re-run, spares within grace). Route auth-gating is in
`tests/test_rbac.py` (the route is in `NO_AUTH_REQUIRED`).
