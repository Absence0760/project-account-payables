# Payment rails — paying your customers' vendors

**Why this matters**: This is the longest-lead-time item on the
critical path. Real ACH out of a real bank takes 3–6 weeks of KYC +
bank onboarding. Start this in **week 1** even if everything else is
parallel.

## Current state

Code-side: the Modern Treasury adapter is implemented and tested. The
payment-run flow (create → submit → webhook → settle) works end-to-end
against Modern Treasury's sandbox. See `backend/docs/payments.md`.

What's missing is **the real bank relationship** — the thing Modern
Treasury fronts.

## The three roles

1. **Modern Treasury** — an abstraction layer over banks. Gives you a
   clean API for ACH / wire / RTP / book transfers, reconciliation,
   ledgering. ~$500/mo starting, then per-transaction.
2. **Your bank** — where your operating cash sits. Becomes the
   funding account for customer payments.
3. **Your customers' banks** — where customer cash sits. Each
   customer authorizes money movement from their bank account into
   their vendor's.

How money flows (typical "sender-pays" model):
```
Customer's operating bank account
    ↓ (NACHA Same-Day ACH pull, or wire)
Your pooled operating account at your bank (Mercury, Brex, etc)
    ↓ (ACH / wire out via Modern Treasury)
Vendor's bank account
```

Alternative: **direct funding** — customer funds each payment
directly from their bank, you never touch the money. Lower regulatory
burden but customer has to authorize every transfer. Bill.com works
this way.

**Recommendation for pilot #1**: direct funding. Less money-movement
licensing exposure.

## Step 1 — Decide sender-pays vs. direct funding

- **Direct funding** (what Bill.com and Tipalti started with):
  - You never hold customer funds.
  - Customer links their bank via Plaid; signs an ACH authorization
    per payment or standing auth.
  - You're not a money-service business → no state MSB licensing.
  - Simpler legally. Harder UX (every payment needs auth).
- **Sender-pays** (what Brex, Ramp, and modern platforms do):
  - Customer prefunds a pooled account.
  - You move money on their behalf.
  - You're a money-service business in most states → MSB registration
    + state-by-state money transmitter licenses ($50K–$2M/year total).
  - Better UX. Higher legal cost. Generally not worth it until
    you're selling 7-figure ACV.

**For pilot #1: direct funding.** Revisit when you hit $1M ARR.

## Step 2 — Modern Treasury onboarding

1. Fill the Modern Treasury pre-sales form on their site.
2. First call covers: product fit, expected volume, compliance
   posture.
3. They send a Letter of Intent + pricing.
4. Sign MSA + DPA (they accept Common Paper's).
5. Complete KYB (Know Your Business): entity docs, EIN, beneficial
   owners, expected transaction volume.
6. They help you pick + onboard a partner bank. Options include
   Silicon Valley Bank, Blue Ridge Bank, Column, Treasury Prime.

Time: 3–6 weeks. Cost: ~$500/mo base + per-transaction.

## Step 3 — Bank onboarding

The partner bank (not MT) actually moves the money.

1. Open an operating account with the partner bank. Typically a
   checking account + the ACH origination agreement.
2. Set up NACHA origination: they give you an ODFI ID + tell you
   which SEC codes (PPD, CCD, WEB) you're authorized for.
3. Set ACH limits — daily/monthly caps they'll allow. Start low
   ($50K/day) and raise as volume grows.
4. Sign the wire agreement separately if you want wires.
5. Get a test account for sandbox. MT ties it to their sandbox.

## Step 4 — Regulatory posture

For direct-funding, you mostly avoid MSB licensing but still need:

- **ACH operating rules**: NACHA requires annual attestation. MT
  runs this for you.
- **OFAC screening**: Every vendor you pay must be screened against
  OFAC SDN. MT does this; just feed them the vendor.
- **Chargebacks / reversals**: NACHA R-code handling. MT's API
  returns these; your code has to consume the webhook and update
  payment state. (This is a code gap — the current adapter handles
  successful settles but not R-code reversals. See
  `backend/docs/payments.md` § TODO.)
- **State money transmission**: Get a legal opinion letter
  confirming your model is *not* money transmission. Cost: $5–15K
  from a payments lawyer. Skipping this is a reasonable risk for
  pilot #1 if direct-funding, but don't raise a Series A without one.

## Step 5 — Production config

Once Modern Treasury + bank are live:

- Set `Organization.settings.payments` per tenant:
  ```json
  {
    "provider": "modern_treasury",
    "mt_api_key": "live_...",
    "mt_originating_account_id": "...",
    "mt_webhook_secret": "...",
    "sandbox": false
  }
  ```
- For direct-funding, the customer's counterparty_id on each vendor
  is their own bank account (verified via Plaid), not yours.
- The payment webhook URL is `https://api.feohledger.com/api/payments/webhook/{tenant_slug}/modern_treasury` — register this in the MT dashboard.
- First real payment: pick an internal invoice (pay yourself from
  the operating account to your personal account) before touching
  customer money.

## Checklist

- [ ] Decision: direct-funding or sender-pays (recommend
      direct-funding for v1)
- [ ] Modern Treasury intro call completed
- [ ] KYB paperwork submitted
- [ ] Partner bank selected + account opened
- [ ] NACHA origination agreement signed
- [ ] Daily/monthly ACH limits set
- [ ] OFAC screening workflow confirmed
- [ ] State MTL legal opinion (if enterprise pipeline demands it)
- [ ] Test payment through sandbox
- [ ] First real payment out successful
- [ ] Webhook → payment status transitions verified in prod

Time: 4–8 weeks calendar; start on day 1.
Cost: ~$500/mo MT + ~$50K legal + bank account fees.
