# Stripe billing — charging customers

**Why this matters**: You can't invoice a customer without a payment
provider. Stripe is the default; Chargebee / Orb / Metronome come later
if you need complex metering.

## What's NOT in code yet

Nothing. There is no billing adapter, no subscription model, no
webhook handler. This is a future engineering task, tracked in
`docs/roadmap.md`.

## What to decide first (before writing code)

### Pricing model

- **Per-seat**: Simple but doesn't scale with value. Good pilot
  pricing: $50/seat/mo minimum 3 seats = $150 floor.
- **Per-invoice-processed**: Aligns with customer value. Typical: $1–2
  per invoice after a free monthly allowance. Harder sell because
  customers can't predict their bill.
- **Tiered bundle**: $500/mo for up to 100 invoices, $1000/mo for up
  to 500, etc. Easiest to sell to SMBs; matches how buyers think.
- **Hybrid platform fee + usage**: $500 base + $1/invoice over 500.
  Best for prospects who want predictability with upside aligned.

For pilot #1: pick a flat monthly bundle. Add metering in v2 when
you have real usage data.

### Trial strategy

- 14-day free trial, no credit card required, auto-expires.
- Or: white-glove pilot (manual contract, free for 30 days, then
  upgrade to paid). Better signal for first customer.

Don't do indefinite free tiers. They attract wrong-fit customers
and cannibalize willingness to pay.

### Annual vs monthly

- Annual contract + monthly invoicing: typical B2B. Customer commits
  to 12 months, pays monthly, you recognize revenue monthly.
- Annual prepay with 10–15% discount: better cash flow, harder to
  negotiate. Fine for customers who pay fast.
- Month-to-month: simplest but highest churn. Avoid for > $500/mo
  plans.

## Step 1 — Stripe account

1. Register at [stripe.com](https://stripe.com) (requires the legal
   entity from `legal-entity.md`).
2. Activate payments (takes 1–3 business days for KYC).
3. Enable **Stripe Billing** (the subscription product).
4. Enable **Stripe Tax** if selling across multiple states —
   automatic sales tax calculation is worth the 0.5% fee.

## Step 2 — Create products + prices

In Stripe Dashboard → Products:

1. Create a product for each pricing tier (e.g. "Starter", "Growth",
   "Business").
2. For each, create a recurring Price (e.g. $500/mo).
3. If metered: create a metered price + set aggregation (`sum` or
   `last_during_period`).
4. Copy the `price_id`s — you'll reference these in code.

## Step 3 — Add billing to the backend

This is the next engineering task. Rough shape:

### Data model

Two new columns on `Organization`:
- `stripe_customer_id VARCHAR(255) NULL`
- `stripe_subscription_id VARCHAR(255) NULL`

Plus a new `Organization.settings.billing` dict with:
```json
{
  "plan_code": "growth",
  "billing_email": "billing@customer.com",
  "status": "active",  // or "trialing", "past_due", "cancelled"
  "current_period_end": "2026-05-23T00:00:00Z",
  "trial_end": null
}
```

### Adapter pattern

Follow the existing adapter pattern (`services/payment_adapters/`,
`services/extraction_adapters/`, etc):

```
services/billing_adapters/
├── __init__.py         # registry
├── base.py             # abstract adapter
├── mock_adapter.py     # for local dev
└── stripe_adapter.py   # real Stripe integration
```

Methods the adapter exposes:
- `create_customer(org) -> customer_id`
- `start_subscription(customer_id, price_id, trial_days) -> subscription_id`
- `cancel_subscription(subscription_id, at_period_end: bool)`
- `report_usage(subscription_id, metric, quantity)` (for metered)
- `parse_webhook(headers, body) -> BillingEvent | None`

### Endpoints

- `POST /api/billing/subscribe` — create + start subscription
  (called from the signup flow)
- `POST /api/billing/portal` — returns a Stripe billing portal URL
  the customer can use to update payment method, download invoices
- `POST /api/billing/webhook/stripe` — HMAC-verified webhook; drives
  `status` transitions on the Organization

### Gating access

New module `services/billing_gate.py` with a `require_active_subscription()`
dependency. Add it to high-value endpoints (invoice upload,
extraction, payment execute) so a past-due tenant is read-only.

Don't block auth/login — past-due tenants need to log in to fix
their billing.

## Step 4 — Usage metering (if metered plan)

The bit most teams underestimate. Approach:

- Instrument the events you want to bill on (e.g. each successful
  extraction, each payment executed).
- Push daily aggregate counts to Stripe via
  `billing.SubscriptionItem.create_usage_record`.
- Keep a local mirror in `Organization.settings.billing.usage` so
  customers see the same number in your UI that Stripe sees.

Don't bill on per-request in real time — if Stripe is down, you drop
billing events. Batch + idempotency key + retry.

## Step 5 — Invoice + dunning

Stripe Billing sends invoices automatically. Configure in
Dashboard → Settings → Billing → Subscriptions:
- Invoice email template (branded)
- Payment retries: 3 retries over 14 days
- Smart retries (Stripe picks the best retry time)
- Auto-cancel after 21 days past due (or whatever your terms say)

## Checklist

- [ ] Pricing model decided + documented
- [ ] Stripe account activated
- [ ] Products + prices created
- [ ] `billing_adapters/` written + wired into signup
- [ ] Webhook endpoint live, HMAC-verified
- [ ] Past-due state gates access
- [ ] Stripe billing portal linked from app settings
- [ ] First test charge works end-to-end (use a
      [Stripe test card](https://stripe.com/docs/testing))

Time: ~1 week (Stripe onboarding is 2 days, integration is 3–5 days).
Cost: Stripe is 2.9% + 30¢ per successful card charge, or 0.8% for ACH.
