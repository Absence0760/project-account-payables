# Payments

## Overview

Payments are the final step in the AP workflow. After an invoice is approved (or sent to ERP), it enters the payment pipeline where it gets scheduled, batched into a payment run, and executed.

```
Invoice Approved
    |
    v
Payment Scheduled          (auto or manual — based on due date & payment terms)
    |
    v
Payment Run Created        (batch of invoices grouped for execution)
    |
    v
Payment Executed           (ACH, wire, check, or virtual card)
    |
    v
Reconciled                 (matched against bank statement — future)
```

## Key Concepts

### Payment Schedule

A payment schedule is created for each approved invoice. It determines *when* to pay based on:

- **Due date** from the invoice
- **Payment terms** (Net 30, 2/10 Net 30, etc.)
- **Early-pay discount** — if terms like "2/10 Net 30" exist, the schedule tracks the discount window

| Field | Type | Description |
|---|---|---|
| invoice_id | UUID | FK to the invoice |
| correlation_id | UUID | Links to the invoice's correlation ID for traceability |
| due_date | Date | When payment is due |
| discount_date | Date | Last day to claim early-pay discount (nullable) |
| discount_percent | Decimal | Discount percentage if paid by discount_date (nullable) |
| payment_terms | String | Original payment terms from the invoice |

**Example:** An invoice with "2/10 Net 30" terms dated April 1:
- `discount_date`: April 11 (10 days)
- `discount_percent`: 2.00
- `due_date`: May 1 (30 days)

### Payment Run

A payment run is a batch of payments executed together. Think of it as "the Friday AP run" — an admin selects invoices to pay, reviews the batch, and executes.

| Field | Type | Description |
|---|---|---|
| status | String | `draft` → `submitted` → `processing` → `completed` / `failed` |
| total_amount | Decimal | Sum of all payments in the run |
| initiated_by | UUID | User who created/executed the run |
| executed_at | DateTime | When the run was executed |

**Payment Run Statuses:**

| Status | Meaning |
|---|---|
| `draft` | Created, invoices selected but not yet submitted |
| `submitted` | Approved for processing |
| `processing` | Payments are being executed (async) |
| `completed` | All payments in the run succeeded |
| `failed` | One or more payments failed |

### Payment

An individual payment record linked to a single invoice. Created when a payment run is executed or when a one-off payment is made.

| Field | Type | Description |
|---|---|---|
| invoice_id | UUID | FK to the invoice being paid |
| payment_run_id | UUID | FK to the batch run (nullable for one-off payments) |
| correlation_id | UUID | Links to the invoice's correlation ID |
| amount | Decimal | Payment amount |
| method | String | `ach`, `wire`, `check`, `virtual_card` |
| status | String | `pending` → `processing` → `completed` / `failed` / `cancelled` |
| reference | String | External reference (check number, wire ref, ACH trace) |

**Payment Statuses:**

| Status | Meaning |
|---|---|
| `pending` | Created, not yet processed |
| `processing` | Payment execution in progress |
| `completed` | Payment confirmed |
| `failed` | Payment failed (insufficient funds, bad account, etc.) |
| `cancelled` | Payment voided before execution |

## User Interface

The payments page (`/payments`) consolidates all payment activity — including virtual cards — into one view with three tabs.

### Page Layout

```
/payments
  ├── Summary Bar        — Total Paid, Pending, Ready to Pay, Payments, Rebates Earned
  ├── Tab: Queue         — invoices ready to pay (sorted by due date, overdue highlighted)
  ├── Tab: History       — all payments in one table (ACH, wire, check, virtual card)
  └── Tab: Runs          — payment batches
```

Virtual cards are **not** on a separate page. Card payments appear in the History tab with a card badge and last-4 digits. This gives one source of truth for "where did the money go."

### Queue Tab

Shows invoices in payable statuses (`approved`, `sent_to_erp`, `posted_in_erp`, `payment_scheduled`) that don't have a completed payment yet.

- Sorted by due date (soonest first)
- Overdue invoices highlighted in red with badge
- Shows payment terms per invoice
- Count shown in the tab badge

### History Tab

All payments across all methods in one table.

| Column | Description |
|---|---|
| Invoice # | Linked invoice number |
| Vendor | Vendor name |
| Method | Badge: ACH, Wire, Check, or **Card** (highlighted) |
| Amount | Payment amount |
| Status | pending / processing / completed / failed / cancelled |
| Reference | Check number, wire ref, ACH trace, or card last-4 |
| Date | Payment date |

Search and status filter chips available on this tab.

### Runs Tab

Payment batch history — each run shows status, total, payment count, and execution date.

### Summary Bar

Five KPI cards at the top:
- **Total Paid** — sum of all completed payments
- **Pending** — sum of pending/processing payments
- **Ready to Pay** — count of invoices in queue
- **Payments** — total payment count
- **Rebates Earned** — sum of virtual card rebates (green, only shown if > 0)

### Virtual Card Security

Card details (full number, CVV) are never stored in the database — only `last_four` and `provider_card_id`.

- Full card details are retrieved on demand from Lithic/Nium via `/api/cards/{id}/details`
- **Access restricted** to admin and AP manager roles — clerks and CFOs get 403
- **Every access is audit-logged** with action `card.details_viewed`
- Details are not cached in the frontend
- In future: require password re-entry before revealing

### Payment Flow

The flow is split into **create draft** and **execute** so a CFO can review what's about to be paid before money moves.

1. User navigates to **Payments > Queue**
2. **Selects invoices** via checkboxes (select-all available)
3. Action bar shows count and total: *"3 selected — $18,050.00"*
4. Clicks **Review & Pay** — review panel slides in
5. **Chooses payment method per invoice** (ACH, Wire, Check, Virtual Card) via dropdown
6. Clicks **Create Draft Run · 3 Invoices** — this:
   - `POST /api/payments/runs` creates a `PaymentRun` with `status='draft'`
   - Pending payment rows are created (no money has moved)
   - The Run Detail modal opens automatically showing the draft
7. Toast: *"Draft payment run created — review and execute"*
8. In the **Run Detail modal** the user reviews the payments table and clicks **Execute**:
   - `POST /api/payments/runs/{id}/execute` flips the run to `completed`
   - Generates payment references (e.g., `ACH-20260406-001`)
   - Updates invoice statuses to `payment_scheduled`
   - **Triggers async ERP sync** in background
9. Toast: *"Payment run executed — 3 payments completed. ERP sync in progress."*
10. Queue clears, History/Runs/Summary update; the modal stays open showing the now-completed run

The same Run Detail modal is reachable by clicking any row in the **Runs** tab — for completed runs it shows the payments + references; for stale drafts it offers Execute.

### Payment processor adapters

The actual money movement is handled by an adapter pattern in `backend/app/services/payment_adapters/` — same shape as ERP, extraction, and card adapters. Each adapter implements:

```python
class PaymentAdapter:
    async def create_payment(payload: PaymentPayload) -> PaymentResult: ...
    async def get_payment_status(provider_payment_id: str) -> PaymentStatus: ...
    def parse_webhook(headers: dict, body: bytes) -> WebhookEvent | None: ...
    async def test_connection() -> bool: ...
```

Registered providers:

| Provider | Methods | Use case |
|---|---|---|
| `mock` | ach, wire, check, rtp, virtual_card | Local dev — settles instantly with deterministic fake references. Default when `Organization.settings.payments` is empty. |
| `modern_treasury` | ach, wire, rtp, check | Production. Real bank rails via Modern Treasury's REST API. Idempotent on `correlation_id`. |

Per-org config lives at `Organization.settings.payments`:

```json
{
  "provider": "modern_treasury" | "mock",
  "program_type": "byok",
  "org_id": "org_...",                // Modern Treasury org ID
  "api_key": "...",
  "originating_account_id": "internal_account_...",
  "webhook_secret": "...",            // HMAC-SHA256 secret for signature verification
  "sandbox": true
}
```

#### Lifecycle

```
[execute payment run]                       [webhook arrives]
        │                                          │
        ▼                                          ▼
adapter.create_payment(payload)            adapter.parse_webhook(headers, body)
        │                                          │
        ▼                                          ▼
 PaymentResult{status, provider_payment_id}    WebhookEvent{provider_payment_id, status}
        │                                          │
        ▼                                          ▼
 Payment row gets:                          Payment row gets:
   provider, provider_payment_id,            status (only if not already terminal),
   reference, status,                        reference, failure_reason,
   submitted_at                              completed_at (on terminal)
```

The orchestrator never auto-completes — only the adapter response (mock) or a webhook (real processor) flips a payment to `completed`. This prevents the platform from claiming money has moved when it hasn't.

#### Webhook URL

Each tenant configures their processor's webhook URL to:

```
https://app.com/api/payments/webhook/{tenant_slug}/{provider}
```

Tenant is encoded in the path (no `X-Tenant-Slug` header needed — processors don't always support custom headers). The adapter verifies the signature; bad signatures, unknown events, and missing payments all return `204` silently to avoid leaking probing information.

#### Adding a new processor (Stripe Treasury, Increase, Column, …)

1. Copy `mock_adapter.py`, implement the four methods.
2. Register with `@register_payment_adapter("stripe_treasury")`.
3. Map the processor's status enum to `PaymentStatus` in your adapter (Modern Treasury's `_STATUS_MAP` is the reference).
4. Add the provider to the org-settings UI dropdown.
5. Tests: dispatcher returns the new adapter, status-map covers every documented status, webhook signature verification works.

### ERP Payment Sync

After a payment run executes, the system syncs payment data to the connected ERP in a background thread:

```
Execute Payment Run (response sent immediately)
    |
    └── Background thread:
        ├── For each payment:
        │   - Push payment details to ERP (amount, method, reference, date)
        │   - Update invoice status: payment_scheduled → paid
        │   - Log: "[payment-sync] Syncing payment abc: invoice=INV-001, $1,500, method=ach"
        |
        └── Log: "[payment-sync] Run xyz: 3 synced, 0 failed"
```

- Sync runs async — **doesn't block** the payment run response
- Uses the same background thread pattern as extraction dispatch (fresh DB engines per thread)
- If no ERP is configured, sync is skipped silently
- Failed syncs are logged for retry (manual retry endpoint planned)

**Files:** `backend/app/services/payment_erp_sync.py`

### 5. Reconciliation (Future)

Matching payments against bank statement entries:

- Import bank statement (CSV/OFX)
- Auto-match by amount + date + reference
- Manual match for unmatched entries
- Flag discrepancies

## API Endpoints

### Implemented

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/payments` | List payments (paginated, filterable) |
| `GET` | `/api/payments/{id}` | Get single payment |
| `POST` | `/api/payments` | Create individual payment |
| `GET` | `/api/payments/runs/` | List payment runs |
| `POST` | `/api/payments/runs` | Create a payment run (draft) |
| `GET` | `/api/payments/runs/{id}` | Get payment run with its payments |
| `POST` | `/api/payments/runs/{id}/execute` | Execute the payment run + trigger ERP sync |
| `GET` | `/api/payments/queue` | List invoices ready for payment |
| `GET` | `/api/payments/summary` | KPIs: total paid, pending, queue count, rebates. Requires a `control_db` dependency because `CardRebate` is a control-plane model; the rebate query includes a try/except fallback returning `0.0` if the `card_rebates` table doesn't exist yet. |

**Query parameters for `GET /api/payments`:**

| Parameter | Type | Description |
|---|---|---|
| `page` | int | Page number (default: 1) |
| `page_size` | int | Items per page (default: 25) |
| `status` | string | Filter by payment status |
| `method` | string | Filter by payment method |
| `invoice_id` | string | Filter by invoice ID |
| `search` | string | Search vendor, invoice number, or reference |
| `amount_min` | float | Minimum amount |
| `amount_max` | float | Maximum amount |

**`POST /api/payments/runs` request body:**
```json
{
  "items": [
    { "invoice_id": "uuid", "method": "ach" },
    { "invoice_id": "uuid", "method": "wire" },
    { "invoice_id": "uuid", "method": "virtual_card" }
  ]
}
```

### Planned

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/payments/runs/{id}/cancel` | Cancel a draft run |
| `GET` | `/api/payments/schedules` | List payment schedules with discount info |
| `PATCH` | `/api/payments/{id}` | Update payment (status, reference) |
| `POST` | `/api/payments/{id}/void` | Void a pending/completed payment |

## Data Model

```
invoices
  |-- payment_schedules (invoice_id FK)
  |       due_date, discount_date, discount_percent, payment_terms
  |
  |-- payments (invoice_id FK)
          amount, method, status, reference
          |-- payment_runs (payment_run_id FK)
                  status, total_amount, initiated_by, executed_at
```

All records carry a `correlation_id` matching the invoice for full traceability via audit logs.

## Payment Methods

| Method | How it works |
|---|---|
| **ACH** | Electronic bank transfer. 1-3 business days. Cheapest option. |
| **Wire** | Immediate bank transfer. Higher fees. Used for large/urgent payments. |
| **Check** | Physical check printed and mailed. Slowest. Used for vendors without electronic payment. |
| **Virtual Card** | Single-use credit card number. Vendor charges the card. Can earn rebates. |

The payment method can be set:
- Per-invoice when creating a payment
- Per-vendor as a default (stored on the vendor record's `bank_details` JSONB)
- Organization-wide default (future)

## Early-Pay Discounts

Many vendors offer discounts for early payment (e.g., "2/10 Net 30" = 2% discount if paid within 10 days).

The payment queue highlights discount opportunities:

- Shows the discount amount (e.g., "$30.00 savings if paid by Apr 11")
- Sorts discount-eligible invoices to the top when the discount window is closing
- The savings are calculated as `invoice.amount * discount_percent / 100`

This helps AP teams prioritize payments that save money.

## Integration Points

### ERP Systems

After payment execution, the payment details (amount, method, reference, date) can be sent to the ERP system alongside the invoice data. The `correlation_id` links the payment back to the original invoice submission.

### Bank / Payment Processor

Payment execution requires integration with a payment processor or bank API:

- **ACH**: Requires bank routing + account number (stored in vendor `bank_details`)
- **Wire**: Requires SWIFT/BIC + IBAN or routing + account
- **Check**: Requires mailing address (vendor's `remit_to_address` or `address`)
- **Virtual Card**: Requires vendor acceptance of card payments

Currently, payment execution is a status change only — actual bank integration is a future phase.

### Audit Trail

Every payment event writes to the audit log:

| Action | Trigger |
|---|---|
| `payment.created` | Payment record created |
| `payment.processing` | Payment execution started |
| `payment.completed` | Payment confirmed |
| `payment.failed` | Payment failed |
| `payment.cancelled` | Payment voided |
| `payment_run.created` | New payment run |
| `payment_run.executed` | Payment run submitted for processing |
| `payment_run.completed` | All payments in run finished |

## Role Access

| Action | Admin | AP Manager | AP Clerk | CFO |
|---|---|---|---|---|
| View payment queue | Yes | Yes | No | Yes |
| Create payment run | Yes | Yes | No | No |
| Execute payment run | Yes | No | No | Yes |
| View payment history | Yes | Yes | No | Yes |
| Void a payment | Yes | No | No | Yes |

CFO approval is required for executing payment runs above a configurable threshold (set in organization settings, future).

## Code Structure

```
backend/app/api/payments.py              # All payment endpoints (CRUD, runs, queue, summary)
backend/app/models/payment.py            # Payment, PaymentRun, PaymentSchedule models
backend/app/schemas/payment.py           # Pydantic schemas
backend/app/services/payment_erp_sync.py # Async ERP sync after payment execution
```

## Implementation Status

| Feature | Status |
|---|---|
| Payment queue (approved invoices ready to pay) | Done |
| Payment history (all methods, filterable) | Done |
| Payment runs list | Done |
| Summary bar (KPIs) | Done |
| Create payment run (select invoices, choose methods) | Done |
| Review panel (per-invoice method selection, total) | Done |
| Execute payment run (complete payments, generate references) | Done |
| Async ERP sync after execution | Done |
| Invoice status update on payment (→ payment_scheduled → paid) | Done |
| Payment schedules (auto-created in seed) | Done |
| Seed data (3 payments, 1 run, payment schedules) | Done |
| Early-pay discount highlighting | Planned |
| Cancel/void payment run | Planned |
| Bank reconciliation | Planned |
| Actual payment processor integration (ACH, wire) | Planned |
| Virtual card generation in payment run | Planned |
