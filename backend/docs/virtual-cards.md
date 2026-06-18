# Virtual Cards

## Overview

Virtual cards are single-use credit card numbers generated per invoice payment. Instead of sending ACH or wire, the system creates a virtual card with a spending limit matching the invoice amount, sends the card details to the vendor, and the vendor charges the card. The card expires after use.

**Why this matters:**
- **Revenue**: 1-2% cashback rebate on every payment — this is the primary monetization channel for AP platforms
- **Security**: Single-use cards can't be reused or overcharged
- **Speed**: Instant payment — no waiting for ACH settlement
- **Control**: Per-card spending limits, merchant restrictions, auto-expiry
- **Reconciliation**: Card charges auto-match to invoices — no manual matching

## Multi-Region Hybrid Strategy

Virtual card issuance is region-specific due to banking regulations. The system uses a hybrid approach with multiple providers:

| Region | Default Provider | Interchange Sharing | Notes |
|---|---|---|---|
| **US** | Lithic | 0.5-1.0% from day one | Best API, fastest integration |
| **UK / EU** | Lithic | 0.5-1.0% from day one | EU coverage via Lithic's European entity |
| **South Africa** | Nium | 0.3-0.8% | Nium covers 40+ countries |
| **Australia, Asia-Pacific** | Nium | 0.3-0.8% | SG, HK, AU, NZ, JP, IN |
| **Canada, Latin America** | Nium | 0.3-0.8% | BR, MX, CA |

**Auto-selection:** The system auto-selects the provider based on the org's region. Admins can override in Organization Settings.

**Graduation path:**
1. **Start** with Lithic (US/UK/EU) + Nium (rest of world) — interchange sharing from day one
2. **Scale** — as card volume grows, negotiate better rates with providers
3. **Graduate** to BIN sponsorship when volume exceeds $10M/month — higher rebates (1.5-2.0%) but requires sponsor bank relationship

### BIN Sponsorship (Future — High Volume)

At >$10M/month card spend, a direct BIN sponsor relationship yields higher interchange:

| Region | Sponsor Banks | Setup Time | Rebate Rate |
|---|---|---|---|
| US | Sutton Bank, Celtic Bank, Column | 3-6 months | 1.5-2.0% |
| UK | Railsr, ClearBank, Modulr | 3-6 months | 1.5-2.0% |
| EU | Railsr, Swan, Treezor | 3-6 months | 1.5-2.0% |
| Other | Not widely available | — | Use Lithic/Nium |

BIN sponsorship is only available in US/UK/EU. Other regions continue using Nium.

### Markets Without Virtual Cards

Some regions don't support virtual card issuance or have low vendor card acceptance. For these markets, fall back to local payment methods:

| Market | Alternative Payment Method |
|---|---|
| South Africa | EFT via local banks, Stitch, Peach Payments |
| India | UPI, NEFT, RTGS |
| Brazil | Pix, boleto |
| Australia | BPAY, direct debit |

Monetize these markets via subscription + per-invoice processing fees instead of card rebates.

## How It Works

```
Invoice Approved
    |
    v
Payment Run (method: virtual_card)
    |
    v
Generate Card                    (via Stripe Issuing / Marqeta / Lithic)
    - Card number, expiry, CVV
    - Limit = invoice amount
    - Linked to invoice + vendor
    |
    v
Send Card to Vendor              (email with card details, or supplier portal)
    |
    v
Vendor Charges Card              (at their payment terminal or online)
    |
    v
Charge Posts                     (webhook from card provider)
    - Payment status → completed
    - Invoice status → paid
    - Rebate earned → tracked
    |
    v
Card Auto-Expires                (single-use, no further charges possible)
```

## Card Lifecycle

| Status | Meaning |
|---|---|
| `created` | Card generated, not yet sent to vendor |
| `sent` | Card details sent to vendor (email or portal) |
| `active` | Vendor has the card, waiting for charge |
| `charged` | Vendor charged the card, payment processing |
| `completed` | Charge settled, payment confirmed |
| `expired` | Card expired without being charged (auto-expire after N days) |
| `cancelled` | Card manually cancelled before use |
| `declined` | Charge attempted but declined (over limit, wrong merchant, etc.) |

## Data Model

### VirtualCard

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| invoice_id | UUID | FK to the invoice being paid |
| payment_id | UUID | FK to the payment record (nullable until charged) |
| vendor_id | UUID | FK to the vendor receiving the card |
| correlation_id | UUID | Links to invoice correlation ID |
| card_provider | String | `stripe`, `marqeta`, `lithic` |
| provider_card_id | String | External card ID from the provider |
| last_four | String(4) | Last 4 digits of card number |
| amount_limit | Decimal | Spending limit (= invoice amount) |
| amount_charged | Decimal | Actual charge amount (nullable until charged) |
| currency | String(3) | Card currency |
| status | String | Card lifecycle status |
| expires_at | DateTime | When the card auto-expires |
| sent_at | DateTime | When card details were sent to vendor |
| charged_at | DateTime | When the vendor charged the card |
| merchant_name | String | Merchant name from the charge (nullable) |
| decline_reason | String | Reason if declined (nullable) |
| organization_id | UUID | Tenant scoping |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### CardRebate

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| virtual_card_id | UUID | FK to the virtual card |
| amount | Decimal | Rebate amount earned |
| rate | Decimal(5,4) | Rebate rate (e.g., 0.0150 = 1.5%) |
| status | String | `pending`, `confirmed`, `paid_out` |
| period | String | Rebate period (e.g., "2026-04") |
| organization_id | UUID | Tenant scoping |
| created_at | Timestamp | |

## User Interface

### Cards Page (`/cards` or tab within `/payments`)

**Card Dashboard (top)**
- Total active cards (count + value)
- Total spend this month via virtual cards
- Rebates earned this month
- Rebates earned YTD
- Card acceptance rate (% of vendors that accept cards)

**Card List (main)**
| Column | Description |
|---|---|
| Card | Last 4 digits + status badge |
| Vendor | Vendor name |
| Invoice | Invoice number (link) |
| Amount | Card limit |
| Charged | Actual charge (blank if not yet charged) |
| Status | Created / Sent / Active / Charged / Completed / Expired |
| Created | Date created |

**Card Actions**
- **Send to Vendor** — email card details (or make available in supplier portal)
- **Cancel** — void the card before it's charged
- **Resend** — resend card details email
- **View Details** — card number (masked), expiry, linked invoice, charge history

### Card Generation in Payment Run

When creating a payment run:
1. User selects invoices and chooses "Virtual Card" as payment method
2. System checks which vendors accept card payments (from vendor record)
3. For eligible invoices, cards are generated in batch
4. Summary shows: X cards generated, total limit $Y, projected rebate $Z
5. User confirms → cards are created and sent to vendors

### Vendor Card Acceptance

Not all vendors accept credit card payments. Track this per vendor:

| Field on Vendor | Type | Description |
|---|---|---|
| accepts_virtual_cards | Boolean | Whether vendor accepts card payments |
| card_contact_email | String | Email to send card details to (may differ from main contact) |
| card_merchant_name | String | Expected merchant name on charges (for matching) |

The payment run UI should:
- Show which invoices can use virtual cards (vendor accepts)
- Suggest ACH/wire fallback for vendors that don't
- Allow the user to override (try card anyway, or force ACH)

## Card Provider Integration

### Stripe Issuing (Alternative)

**Why**: Best documentation, good if you already use Stripe. However, Stripe does not share interchange by default — requires negotiation at scale.

**Setup:**
1. Apply for Stripe Issuing (requires Stripe account + Issuing approval)
2. Store API keys in Organization settings
3. Create a cardholder (represents your company)
4. Generate cards per invoice via API

**Create a virtual card:**
```python
import stripe

card = stripe.issuing.Card.create(
    cardholder="ich_...",
    type="virtual",
    currency="usd",
    spending_controls={
        "spending_limits": [
            {"amount": 150000, "interval": "per_authorization"}  # $1,500.00
        ],
    },
    metadata={
        "invoice_id": "...",
        "correlation_id": "...",
        "vendor_name": "Office Supplies Co",
    },
)
```

**Get card details (to send to vendor):**
```python
details = stripe.issuing.Card.retrieve(
    card.id,
    expand=["number", "cvc"],
)
# details.number = "4242424242424242"
# details.cvc = "123"
# details.exp_month = 12
# details.exp_year = 2026
```

**Webhooks:**
- `issuing_authorization.created` — vendor is attempting to charge
- `issuing_authorization.updated` — charge approved/declined
- `issuing_transaction.created` — charge settled

**Rebate:** Stripe doesn't pay rebates directly. You'd negotiate interchange rebates through a Stripe Issuing partner program or use a BIN sponsor.

### Marqeta (Enterprise)

**Why**: Better spend controls, more card program customization, interchange rebate programs.

**Create card:**
```
POST /cards
{
    "card_product_token": "...",
    "user_token": "...",
    "metadata": {
        "invoice_id": "..."
    }
}
```

**Webhooks:** Real-time authorization decisions via JIT (Just-in-Time) funding.

**Rebate:** Marqeta offers interchange revenue sharing as part of their card program.

### Lithic (Developer-Focused)

**Why**: Simple API, good for startups, fast onboarding.

**Create card:**
```
POST /cards
{
    "type": "SINGLE_USE",
    "spend_limit": 150000,
    "spend_limit_duration": "TRANSACTION",
    "memo": "INV-2024-001 - Office Supplies Co"
}
```

**Webhooks:** Authorization events, settlement events.

**Rebate:** Lithic offers interchange sharing programs.

## Organization Settings

Stored in `Organization.settings.cards`:

```json
{
    "cards": {
        "enabled": true,
        "provider": "stripe",
        "api_key": "sk_live_...",
        "cardholder_id": "ich_...",
        "default_expiry_days": 30,
        "auto_send_to_vendor": true,
        "rebate_rate": 0.015,
        "notification_email_template": "default"
    }
}
```

## Rebate Economics

Virtual card rebates are based on interchange fees. When a vendor charges a virtual card, the card network (Visa/Mastercard) charges the vendor's bank an interchange fee (typically 1.5-3% for commercial cards). A portion of that comes back as a rebate.

**Typical rebate rates:**

| Provider | Rebate Rate | Notes |
|---|---|---|
| Stripe Issuing | 0% (direct) | Need partner program for interchange |
| Marqeta | 0.5-1.5% | Revenue share on interchange |
| Lithic | 0.5-1.5% | Revenue share on interchange |
| Direct BIN sponsor | 1.0-2.0% | Highest rates, most complex setup |

**Example economics:**
- $1M monthly AP spend via virtual cards
- 1.5% rebate rate
- = $15,000/month rebate revenue
- = $180,000/year

This revenue often covers the entire cost of the AP platform for the customer, making virtual cards a strong sales tool ("the platform pays for itself").

## Vendor Communication

### Email Template

When a card is generated and sent to a vendor:

```
Subject: Payment for Invoice INV-2024-001 — Virtual Card Details

Hi Office Supplies Co,

Payment for Invoice INV-2024-001 ($1,500.00) has been issued via virtual card.

Card Details:
  Card Number: 4242 4242 4242 4242
  Expiry: 12/2026
  CVV: 123
  Amount: $1,500.00

Please charge this card within 30 days. The card is single-use and will expire after the first charge.

If you have questions, reply to this email or contact our AP team.

— Acme Corp Accounts Payable
```

### Supplier Portal Integration

If the supplier portal is implemented, vendors can:
- View pending card payments
- Copy card details securely (masked until clicked)
- Mark when they've charged the card
- See payment history

## API Endpoints

### Planned

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/cards` | List virtual cards (filterable by status, vendor, date) |
| `GET` | `/api/cards/{id}` | Get card details (masked by default) |
| `GET` | `/api/cards/{id}/details` | Get full card number + CVV (audit logged) |
| `POST` | `/api/cards/generate` | Generate cards for selected invoices |
| `POST` | `/api/cards/{id}/send` | Send card details to vendor |
| `POST` | `/api/cards/{id}/cancel` | Cancel an unused card |
| `POST` | `/api/cards/webhook/{provider}` | Receive charge/settlement webhooks |
| `GET` | `/api/cards/rebates` | List rebates by period |
| `GET` | `/api/cards/dashboard` | Card program KPIs |

## Security

- Full card numbers are only shown on explicit request (with audit log entry)
- Card details are never stored in our database — retrieved from provider on demand
- Cards have strict spending limits matching invoice amounts
- Cards auto-expire after configurable period (default: 30 days)
- Declined charges trigger alerts
- All card operations require authentication
- Provider API keys stored encrypted in org settings (future: secrets manager)

### Audit trail (every card state change is logged)

Card lifecycle transitions write an append-only `audit_log` row
(`entity_type="virtual_card"`) so the money + PII path is fully
reconstructable for SOX. **No row ever carries the full PAN or CVV** —
the `details` payload records only `last_four` plus the from/to status
and (for charges/rebates) the exact **string-Decimal** amount, never a
float.

| Action | When | `details` |
|---|---|---|
| `card.details_viewed` | PAN reveal (`GET /{id}/details`) | `last_four` |
| `card.cancelled` | manual cancel (`POST /{id}/cancel`) | `last_four`, `from`, `to` |
| `card.charged` | authorization webhook applies a charge | `last_four`, `from`, `to`, `amount_charged` (string Decimal) |
| `card.settled` | settlement webhook completes + accrues the rebate | `last_four`, `from`, `to`, `rebate_amount`, `rebate_rate` (string Decimals) |

### Webhook handler (`POST /cards/webhook/{provider}`)

Provider charge/settlement callbacks are **unauthenticated** (they come
from Lithic / Nium, not a logged-in user) and verified by HMAC over the
raw body against the owning tenant's
`Organization.settings.cards.webhook_signing_secret`. The handler:

1. parses `card_token` + `event_id` from the provider-specific body,
2. finds the owning tenant by `provider_card_id`,
3. verifies the HMAC (`verify_hmac_sha256`) — missing/forged signature is rejected,
4. dedupes by `event_id` (`is_event_already_processed`, Redis `SET NX`) so a re-delivery is a no-op,
5. applies the state change + writes the audit row in a single committed transaction.

**Every rejection path returns `204` silently** (bad/missing signature,
unknown card token, missing event id, malformed JSON) — a distinct 4xx
would let an attacker enumerate card tokens or tenant slugs.

E2E coverage: `frontend/tests-e2e/cards/` — `webhook-security.spec.ts`
(HMAC reject + dedup + silent 204 + PII-free charge audit),
`lifecycle.spec.ts` (issue → cancel audit + 409 double-cancel),
`pan-reveal-pii.spec.ts` (role gate + last-four-only audit),
`rebate-and-isolation.spec.ts` (exact-Decimal rebate + tenant scoping).

## Code Structure

```
backend/app/services/card_adapters/
    __init__.py          # Package exports
    base.py              # CardAdapter base class, VirtualCardPayload, CardResult types
    dispatcher.py        # get_card_adapter() — auto-selects by region, REGION_DEFAULTS map
    mock_adapter.py      # Development/testing adapter
    lithic.py            # Lithic adapter (US/UK/EU) — interchange sharing from day one
    nium.py              # Nium adapter (40+ countries) — global coverage

backend/app/models/virtual_card.py    # VirtualCard + CardRebate SQLAlchemy models
backend/app/schemas/virtual_card.py   # Pydantic schemas
backend/app/api/cards.py              # API endpoints (generate, list, cancel, details, webhook, rebates)
```

## Implementation Status

| Phase | Status |
|---|---|
| Card adapter interface + dispatcher | Done |
| Mock adapter | Done |
| Lithic adapter (US/UK/EU) | Done |
| Nium adapter (global) | Done |
| VirtualCard + CardRebate models | Done |
| Card API endpoints (generate, list, cancel, details, webhook, rebates) | Done |
| Card config in organization settings UI | Done |
| Region-based auto provider selection | Done |
| Card list page frontend | Planned |
| Card generation in payment run UI | Planned |
| Vendor email notification | Planned |
| Vendor card acceptance tracking | Planned |
| Rebate dashboard UI | Planned |
| Supplier portal integration | Planned |
| BIN sponsor graduation | Future (>$10M/month)
