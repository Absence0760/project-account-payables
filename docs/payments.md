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

## User Flow

### 1. Payment Queue

The Payments page shows invoices that are approved/sent_to_erp but don't have a completed payment yet. This is the "ready to pay" queue.

- Sorted by due date (soonest first)
- Highlights overdue invoices
- Shows early-pay discount opportunities with savings amount
- Filterable by vendor, amount range, due date range

### 2. Create Payment Run

1. User navigates to **Payments** and clicks **Create Payment Run**
2. Selects invoices from the payment queue (checkboxes)
3. Chooses payment method (ACH, wire, check, virtual card) — can be different per invoice
4. Reviews the batch: total amount, invoice count, payment method breakdown
5. Clicks **Submit** to create the run in `draft` status
6. Clicks **Execute** to move to `processing` and trigger payment execution

### 3. Payment Execution

When a run is executed:

1. Each selected invoice gets a `Payment` record with status `pending`
2. Payments are dispatched to the payment processor (async)
3. As each payment completes, its status updates to `completed` or `failed`
4. When all payments in the run finish, the run status updates to `completed` (or `failed` if any failed)
5. An audit log entry is written for each payment event

### 4. Payment History

The payment history view shows:

- All past payment runs with status, total, date, and payment count
- Individual payments with vendor, invoice number, amount, method, status, and reference
- Drill into a payment run to see its individual payments
- Filter by status, method, date range

### 5. Reconciliation (Future)

Matching payments against bank statement entries:

- Import bank statement (CSV/OFX)
- Auto-match by amount + date + reference
- Manual match for unmatched entries
- Flag discrepancies

## API Endpoints

### Existing Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/payments` | List payments (paginated, filterable) |
| `GET` | `/api/payments/{id}` | Get single payment |
| `POST` | `/api/payments` | Create individual payment |
| `GET` | `/api/payments/runs/` | List payment runs |

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

### Planned Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/payments/runs` | Create a payment run (draft) |
| `GET` | `/api/payments/runs/{id}` | Get payment run with its payments |
| `POST` | `/api/payments/runs/{id}/add` | Add invoices to a draft run |
| `POST` | `/api/payments/runs/{id}/remove` | Remove invoices from a draft run |
| `POST` | `/api/payments/runs/{id}/execute` | Execute the payment run |
| `POST` | `/api/payments/runs/{id}/cancel` | Cancel a draft or submitted run |
| `GET` | `/api/payments/queue` | List invoices ready for payment |
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

## Implementation Order

1. **Payment queue** — query approved invoices without completed payments, show due dates and discount opportunities
2. **Create payment run** — select invoices, choose methods, review totals, save as draft
3. **Execute payment run** — submit for processing, create individual payment records, update statuses
4. **Payment history** — list runs and individual payments with filters
5. **Payment schedules** — auto-create on invoice approval based on payment terms
6. **Reconciliation** — bank statement import and matching (future phase)
7. **Bank integration** — actual payment processor API calls (future phase)
