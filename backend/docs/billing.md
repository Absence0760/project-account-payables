# Platform Billing & Metering

How the platform bills its own customers (the orgs/tenants) — plans,
subscriptions, usage metering, entitlement gating, and the pluggable billing
provider. This is the AP platform's *own* revenue plumbing, distinct from the
accounts-payable money path the app manages for customers.

> **Status — FIRST SLICE.** Shipped: the control-plane plan/subscription model,
> a usage rollup off the existing meters, a `mock`-default billing adapter family
> (+ fail-closed `stripe_billing` skeleton), an entitlement gating helper wired
> onto the public `/api/v1` surface, and a customer read endpoint. **Deferred to
> later slices:** real Stripe API calls, dunning / past-due automation,
> proration, plan-change / payment-method / invoice-list endpoints, and the
> customer billing UI.

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
| `stripe_billing` | Skeleton — live key via sops, **fails closed** (`BillingNotConfigured`) without `AP_BILLING_STRIPE_API_KEY`. The provider API calls are documented `TODO(jared)` skeletons for the next slice, but the **wire shape is correct** and `parse_webhook` IS implemented end-to-end: it verifies the `Stripe-Signature` HMAC over the raw body via `services/webhook_security.verify_hmac_sha256` and maps Stripe statuses → our four-state lifecycle. **Webhook invariant:** the route that consumes these events must dedupe by `event_id` via `webhook_security.is_event_already_processed` and 204 silently on any rejection (the webhook route itself is a later slice). |

`get_billing_adapter(provider=None)` resolves: explicit arg → `AP_BILLING_PROVIDER`
→ `mock`. An unknown name falls back to `mock` (a bad config can't break read
paths). Per-org override: `Organization.settings.billing.provider`.

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

## Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_BILLING_PROVIDER` | `mock` | Billing adapter — `mock` (local-first default) \| `stripe_billing`. Per-org override `Organization.settings.billing.provider`. |
| `AP_BILLING_STRIPE_API_KEY` | (empty) | Live Stripe Billing secret key — **no hardcoded fallback**; sops in deployed. The `stripe_billing` adapter fails closed without it. |
| `AP_BILLING_STRIPE_WEBHOOK_SECRET` | (empty) | HMAC secret for Stripe webhook signature verification — no fallback; sops in deployed. |

## Tests

`backend/tests/test_billing.py` — adapter default + fallback, mock determinism,
Stripe fail-closed + webhook HMAC verify/reject, entitlement allow/deny, rollup
Decimal-exactness, the subscription endpoint (plan + status + usage, admin/cfo
gating, null-plan case), and the `/api/v1` plan-gate (402 without `public_api`,
200 with). The control-tables coverage test in
`tests/test_tenant_provisioning.py` includes `plans` + `subscriptions`.
