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

**Payment Run Statuses:** (the full set — `schemas/payment.py::PaymentRunStatus`)

| Status | Meaning |
|---|---|
| `draft` | Created, invoices selected but not yet submitted |
| `executing` | Claimed by `/execute`; the per-payment dispatch loop is running |
| `submitted` | At least one payment is in flight, waiting on a processor webhook |
| `processing` | Payments are being executed (async) |
| `partial` | At least one payment succeeded AND at least one failed |
| `completed` | Every payment in the run succeeded |
| `failed` | Every payment failed |
| `cancelled` | A draft run cancelled before execution; its payment rows were deleted |

The bucketing and the precedence that turns payment outcomes into a run status
live in ONE place — `services/payment_runs.py::rollup_payment_statuses` /
`PaymentRunRollup.run_status` — shared by the dispatcher that *persists*
`run.status` and by the run-detail / runs-list reads that *report* it, so the
two can't drift.

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

### Segregation of duties on payment runs (maker-checker)

The user who **creates** a payment run cannot also **execute** it (the
money-movement step) or **CFO-approve** it. Enforced in
`services/payment_controls.check_run_segregation` by comparing the actor's id to
the run's `initiated_by`, wired into both `POST /runs/{id}/execute` and
`POST /runs/{id}/approve` (both return **403** for the run's creator). A
different user must perform the second step.

This is **orthogonal to the role/permission split**. `require_permission`
separates `payment_run.approve` from `payment_execute` by *role*, but the
default `ap_manager` holds both, so without this identity check a single
`ap_manager` could create a run and immediately execute it — the entire payment
lifecycle with no second human. The control mirrors the invoice-approval
`check_segregation` (uploader ≠ approver).

**Default-on.** Single-operator accounts opt out per-org with
`Organization.settings.payments.require_run_segregation: false` (any value other
than an explicit `false` keeps the secure default). A legacy run with a NULL
`initiated_by` is never blocked (nothing to compare against).

### Every by-id route is entity-scoped

Multi-entity Phase 2 scopes reads and writes by the `X-Entity-ID` header. The
list surfaces (`GET /payments`, `/queue`, `/summary`, `/counts`, `/runs/`) have
honoured it since that landed, but every **by-id** route used to resolve its
row on the primary key alone — `GET /payments/{id}`, `/{id}/remittance`,
`POST /{id}/void`, `/{id}/compliance/{release,dismiss}`, `POST /payments`,
`GET /runs/{id}`, and `POST /runs/{id}/{approve,cancel,execute,retry-failed,resume}`.
Inside one tenant that let a user with subsidiary A selected read, void,
release, CFO-approve and execute subsidiary B's money simply by knowing the id:
the entity selector was advisory on exactly the routes that move money.

`_get_scoped_payment` / `_get_scoped_run` in `api/payments.py` are the fix,
mirroring `api/positive_pay.py::_get_scoped_file` on the sibling treasury
router. Two properties matter:

- **Opaque 404.** An out-of-scope id returns the same `Payment not found` /
  `Payment run not found` as one that doesn't exist — never a 403 — so the
  response can't be used to enumerate another subsidiary's payments.
- **The row locks are unchanged.** The scope predicate is one more `WHERE`
  clause on the same `SELECT ... FOR UPDATE`; pass `for_update=True` on the
  mutating callers (see the section below).

The consolidated view (no header, or `X-Entity-ID: all`) still sees every
entity's rows, so single-entity tenants and pre-multi-entity API consumers are
unaffected. Pinned by `tests/test_payment_entity_scope.py`.

### Every run-state endpoint row-locks

`POST /runs/{id}/approve`, `/execute`, `/resume` and `/cancel` all read the
run with `SELECT ... FOR UPDATE`, and `POST /payments/{id}/void` (plus the two
compliance handlers) lock the payment the same way. The lock is not
belt-and-braces on any of them — each guards a distinct double-spend or
double-record race, and a plain `SELECT` there means two requests both pass
the status guard.

`/cancel` was the exception, and the most dangerous one, because it is the
endpoint that **deletes** the run's child `Payment` rows. `/execute` locks,
flips the run to `executing`, then commits — releasing the lock — *before* its
adapter loop, so an unlocked `/cancel` that had already read `draft` sailed
past the guard and deleted the very payments being handed to the processor.
Both outcomes were reproducible against the unfixed code: the canceller
winning outright (payments deleted, adapter never called, the run reporting
success having paid nothing) and the rows vanishing mid-dispatch (real money
out, no `Payment` record, run reads `cancelled`). Pinned by
`tests/test_payment_concurrency.py::test_cancel_racing_execute_cannot_delete_dispatched_payments`
— BUG D in that file's header.

**Adding a run- or payment-state endpoint? Lock the row.** The file's existing
handlers each carry a comment naming the race their lock prevents; follow the
pattern rather than assuming the status guard alone is enough.

### Execution atomicity + resuming a stuck run

`execute_payment_run`'s per-payment loop is durable, not all-or-nothing: each
payment is dispatched (via the internal `_execute_single_payment`) inside its
own try/except catch-all, then committed immediately — a failure on payment N
(including an uncaught error from a live FX/sanctions/processor adapter, not
just the anticipated `InternationalPaymentError`) can only roll back payment
N's own still-open attempt, never the payments already recorded before it.

A worker crash mid-loop leaves the run at `status="executing"` with every
payment up to the crash point already safely committed with its real
outcome, and everything after it still `pending`. `POST
/runs/{id}/resume` (same `payment.execute` permission gate) re-drives only
those still-`pending` payments and re-rolls-up the run's final status across
*every* payment on the run — nothing already settled is re-sent to the
processor. `POST /runs/{id}/execute` deliberately stays `draft`-only rather
than also accepting `executing`: a run that is still genuinely mid-execution
is also `executing`, and letting `/execute` resume it too would let a
concurrent call race an actively-running execution instead of only a
confirmed-stuck one (see the row-lock double-execute guard above). An
operator calls `/resume` only after confirming the run has made no progress
for an implausible amount of time.

### The live-payment 409 names the invoices

`uq_payments_one_live_per_invoice` is the DB-level backstop that stops an
invoice from carrying two live payments. When a run trips it the operator got
"One or more invoices already have a live payment scheduled." — which
identifies nothing; on a forty-invoice Friday run, bisecting the selection by
hand was the only way forward. The 409 now names the offending invoice
**numbers** (the identifier the row was selected by, and PII-free) and says what
to do about them.

Naming them requires the session to still be usable after the `IntegrityError`,
so `create_payment_run_for_invoices` now always wraps its inserts in a savepoint
— it used to do that only on the copilot's `plan_id` path, and on the manual
path the poisoned session was precisely why the message could not say more.

### Credit memos are netted on BOTH money paths

Applying a credit memo is the whole point of the feature: it must reduce what
the vendor is actually paid. The payment-run builder has netted applied memos
off each payment since that fix landed — but `POST /api/payments`, the
standalone path, did not. It bound the payment to a bare `invoice.amount` and
422'd any other figure, so a credited invoice paid the vendor the **full
pre-credit amount** there, and a caller who knew the correct net figure could
not even submit it. Every guard around *applying* a memo (vendor match,
currency match, no over-application) was solid, and none of it mattered on that
path.

Both paths now call `services/payment_runs.net_payable_amount(db, invoice)` —
`invoice.amount` minus the sum of its `applied` credit memos — so they can't
disagree about what an invoice is worth. Consequences on the standalone
endpoint:

- `PaymentCreate.amount` is now **optional**. When supplied it is only a
  cross-check against the net figure; the server never trusts it as the amount
  (that guard is what stops a $99,999 payment against a $500 invoice).
- The CFO-approval threshold compares the **net** amount — the money that
  actually moves — exactly as `create_payment_run` compares its netted total.
- A fully-credited invoice is refused with **409**: nothing is owed, and a
  zero-amount payment row would be a money record for money that never moves.

`credit_memos.py`'s own over-application guard (apply refuses a memo that would
exceed the invoice's remaining creditable balance) is what guarantees the net
can never go negative. Pinned by
`tests/test_payment_create_credit_memo_netting.py` (standalone) and
`tests/test_payment_run_credit_memo_netting.py` (runs).

### Why a payment failed, and retrying it

`Payment.failure_reason` is written on every failure path — compliance refusal,
card-issuance failure, an adapter raising, a void, a webhook-reported failure —
but for a long time it never left the database, and the partial-failure counts
existed only in the transient response body of the `/execute` call that
produced them. Reload the page and a `partial` run was a bare status word with
no way to ask "which ones, and why?" except reading the server log.

Now:

- `PaymentResponse` and the run-detail payments carry `failure_reason`,
  `provider`, `submitted_at`, `completed_at`.
- The run detail and the runs list carry the per-outcome rollup
  (`payments_completed` / `payments_failed` / `payments_in_flight` /
  `payments_pending`), **derived on read** from the child `Payment` rows — no
  stored running total, so it can't drift from the payments it summarises. (The
  runs list computes all of them in one grouped query; it used to issue a count
  per run.)
- `POST /runs/{id}/retry-failed` re-attempts them.

**`/retry-failed` in one paragraph.** Accepted only on a `partial` / `failed`
run (`draft` → use `/execute`; `executing` → use `/resume`). Same
`payment.execute` permission, the same maker-checker `check_run_segregation`,
and the same CFO-threshold gate as `/execute` — skipping any of those would
make retry a way around all three. It books a second attempt for the failed
payments it judges safe, then re-drives the run through the same
`_dispatch_run_payments` loop, so anything already `completed` / `submitted` /
`processing` / `pending_compliance` is never re-sent to the processor.

#### A retry books a NEW payment; it never re-arms the old one

`Payment.correlation_id` is the **processor's idempotency key**, not a local
trace id: `payment_adapters/base.py` says so, `column` / `dwolla` /
`stripe_treasury` / `increase` send it as `Idempotency-Key`, `modern_treasury`
as `idempotency_key=`, and `checkeeper` burns it into a 48-hour Redis `SET NX`
slot explicitly so a retry can't print a second physical cheque.

A re-attempt is genuinely a new order, so it needs a new key. Minting one onto
the *failed row* — which is what this endpoint originally did — also meant
clearing that row's `failure_reason`, `provider_payment_id`, `submitted_at` and
`completed_at`: it destroyed the only handles anyone had for reconciling
attempt #1 with the processor, and overwrote two regulated money timestamps.

So attempt #1 is never written to at all. Attempt #2 is an **INSERT** on the
same run carrying `retry_of_payment_id` (migration `0080`, tenant-scoped),
which the `payments` rollups use to count the **latest attempt per invoice**
(`services/payment_runs.active_run_payments`) rather than every row ever — all
three call sites (`_dispatch_run_payments`, the run detail, the runs list) go
through it, so a fully recovered run reports `completed` instead of `partial`
forever. `failed` / `cancelled` sit outside `uq_payments_one_live_per_invoice`,
which is what lets the second row claim the invoice's live-payment slot; a
savepoint per insert is the backstop for a conflict arriving as a race. The
superseded attempt stays visible on the run detail — an operator has to be able
to see that an invoice took two goes — it just isn't counted twice.

#### Only a failure we can prove never reached the processor is re-attempted

`services/payment_runs.classify_payment_failure` (pure, unit-tested) is the
gate. A failed payment is **in doubt** — and skipped as `needs_reconciliation`
for a human to void or reconcile — when any of these hold:

| Signal | Why it's in doubt |
|--------|-------------------|
| `provider_payment_id` populated | An order exists at the processor; every adapter here returns a handle only from a create call that succeeded. |
| `unexpected_error:*` | The dispatcher swallowed an exception. A read timeout *after* the processor accepted looks identical to one before it. |
| `*_transport_error:*` | The request may well have been received and actioned before the connection died. |
| `*_api_error:*` | The provider answered — a 5xx can still have created the order. |
| `reconciler_max_age_exceeded*` | A genuinely `submitted` payment, real money in flight, that `payment_reconciler` gave up waiting on. **This is the case that made the old re-arm a double-pay.** |
| `checkeeper_duplicate_suppressed` | The 48-hour print slot was already claimed — a cheque for this order was very likely already printed. |
| `adapter_error:*` (card leg) | The card provider may have minted a card we never recorded. |
| blank / unrecognised reason | Fail-closed: a future adapter or a legacy row is not waved through. |

Everything else — our own `compliance_refusal:` / `compliance_dismissed` /
`international_payment_error:`, the card leg's `card_issuance_*` /
`cards_not_enabled`, and each adapter's pre-flight refusals (`*_not_configured`,
`*_no_counterparty`, `*_no_external_account`,
`*_no_destination_funding_source`, `*_missing_mailing_address`,
`*_idempotency_unavailable`, `method '…' is not supported by …`) — provably
never left this process, so re-sending is safe.

**Adapter authors:** a *pre-flight* refusal must use one of those codes, not
free prose, or it classifies as unrecognised and can never be auto-retried.
Modern Treasury's two pre-flight refusals were prose and were normalised for
exactly this reason.

#### The other skip reasons

Every skip is reported back in `skip_reasons`; nothing skipped is mutated or
re-sent.

- `invoice_not_payable` — the invoice is voided, re-rejected or already `done`,
  so paying it would move money against something nobody currently approves.
- `invoice_has_blocking_exception` — an unresolved
  `PAYMENT_BLOCKING_EXCEPTION_TYPES` flag (`duplicate` / `fraud_flag` /
  `line_total_mismatch`). Run creation refuses these outright; this endpoint
  re-dispatches money days or weeks later, so a `fraud_flag` raised in the
  interim (a BEC bank-detail swap, an altered or never-issued cheque off a
  Positive Pay return) has to stop the re-send here too. Both callers share
  `services/payment_runs.blocked_invoice_ids` so they can't drift.
- `net_amount_changed` — a credit memo applied while the payment sat `failed`
  (`credit_memos.py` gates on neither invoice status nor an existing payment)
  means the failed row's `amount` is no longer what the vendor is owed. The
  retry re-derives `net_payable_amount` and **skips**; the amount is never
  silently adjusted, so the operator builds a fresh run through the full gate
  set.
- `invoice_has_live_payment` — the invoice has since acquired another live
  payment.

`retryable_failures` on the run detail counts only failures the endpoint will
actually re-attempt, so the retry button can never offer an action that could
only be skipped.

**Idempotency** comes from the run claim: the row is locked and flipped to
`executing` before anything is booked, so a double-click blocks on the lock and
then 409s against the non-retryable status.

Every retry writes a `payment.retried` audit row naming **both** payment ids
(the superseded attempt and its successor) plus the failure reason it replaced,
and one `payment_run.retried` row for the batch. Pinned by
`tests/test_payment_run_retry.py`.

### The `virtual_card` leg — converging on an invoice's existing card

A `virtual_card` payment skips the payment adapter and mints a card instead
(`_execute_single_payment` → `services/card_issuance`). Because an invoice can
hold at most one LIVE card (`uq_virtual_cards_one_live_per_invoice`), the leg
has to cope with that card already existing — it may have been minted by `POST
/api/cards/generate`, or by a concurrent payment run. Both are reachable, so
the leg mirrors the batch endpoint's two-layer handling:

1. **Pre-check** — `find_live_card_for_invoice` before the provider call. An
   invoice that already holds a live card never reaches the adapter, so no
   second spendable card is minted and then orphaned by the index rejecting its
   row.
2. **Savepoint** — the insert goes through `persist_card`, which flushes inside
   a `begin_nested()` block. A racer that claimed the slot between the
   pre-check and the flush trips the index; the savepoint contains it so the
   dispatch loop's audit row and per-payment commit still succeed. Without it
   the `IntegrityError` left the whole session needing a rollback, so the
   loop's very next statement raised `PendingRollbackError`, the run never
   rolled up, and it stayed `executing` — with `/resume` re-driving the same
   payment into the same crash.

The payment then settles against whichever card is live — but **only if that
card can actually be what settled it** (`card_issuance.card_settlement_block`).
Converging marks the payment `completed`, i.e. asserts money moved, so the
assertion has to be true. Two honest failure states rather than a misleading
`completed`:

| `failure_reason` | When |
|---|---|
| `card_already_charged` | the occupying card is `charged`/`completed` — its funds already moved, under a *different* payment |
| `card_already_issued_insufficient_limit` | the card is live and unspent but its `amount_limit` is below this payment's amount |
| `card_issuance_conflict` | the contended live-card slot was empty on re-read (the winner cancelled its card in between) — surfaced for AP rather than silently re-calling the provider |

The spent-card case is **not** a race. `amount_limit` is the authorization
ceiling and is never reduced by spend (a charge only sets `amount_charged`), so
a limit-only check would happily settle against a redeemed card. The reachable
flow is: mint → the vendor redeems it (webhook → `charged`) → AP voids that
payment → the invoice returns to the payable pool (`approved` is in
`PAYABLE_INVOICE_STATUSES`) → the next run finds the same spent card.

When convergence *is* valid, the payment claims the card's `payment_id` if
nothing else owns it (a card from `POST /api/cards/generate` carries none) —
`list_payments` resolves a row's card via `VirtualCard.payment_id ==
Payment.id`, so without that link the UI shows no card on a payment whose
reference reads `CARD-…`. A card already naming another payment is never
re-pointed; that payment is live and the link is its badge.

Both outcomes write a PII-free audit row against the card (ids, last four,
exact amount as a string — never a PAN): `card.generated` on a fresh mint
(matching the batch endpoint, so a card-lifecycle query shows a creation event
on both mint paths) and `card.reused` when the payment settled against a card
it did not mint. A converged payment does **not** re-email the vendor — the
reveal link was already sent when the card was minted.

### Voiding a card payment cancels the card

`POST /payments/{id}/void` on a `virtual_card` payment also closes the card at
the provider (`_cancel_card_for_void` → `card_issuance.cancel_card_at_provider`).
Without that, the void only moved our books: the card stayed live and
bearer-spendable with no payment behind it, and — still occupying the invoice's
live-card slot — it was rediscovered by the next payment run.

Provider-**first**, mirroring `POST /api/cards/{id}/cancel`: the row flips to
`cancelled` (plus a `card.cancelled` audit row tagged `via: payment_void`) only
once the provider confirms the close. Best-effort like the payment rail — a
card-provider outage records the outcome instead of blocking the accounting
void. The outcome lands on the `payment.voided` audit row as `card_outcome`:

| `card_outcome` | Meaning |
|---|---|
| `card_cancelled` | closed at the provider and in our DB |
| `card_already_charged` | already spent — the provider cannot un-spend it; AP must chase the refund. `card_settlement_block` is what then stops a later run settling against it |
| `card_already_cancelled` / `no_card_linked` | nothing to do |
| `cards_not_configured` / `card_cancel_rejected` / `card_cancel_error:<Type>` | the provider could not confirm; the card is left live (unverified cancels are never recorded) |

### Sanctions / compliance hold resolution

`_execute_single_payment` parks a payment at `status="pending_compliance"`
in two cases: the invoice has no screenable vendor at all, or
`services/compliance.check_payment_compliance` itself returns a `hold`
verdict (sanctions/KYC review required). Both cases open a
`payment_compliance_hold` Exception (`exception_type`, severity `error`)
scoped to the invoice — deduplicated on `(invoice_id,
"payment_compliance_hold", "open")` so a retried/resumed execution never
opens a second one for the same hold. This is what makes the hold visible
in the normal exceptions queue instead of only in the payment's own
`failure_reason` field.

Two endpoints resolve it. Neither requires the invoice to actually own an
open `payment_compliance_hold` exception — resolving it is best-effort/a
no-op if none exists — the payment's own `pending_compliance` status is
the real gate:

- **`POST /payments/{id}/compliance/release`** (`payment.execute`) —
  re-runs the exact same compliance-then-adapter path
  (`_execute_single_payment`) that produced the hold. This is deliberate:
  it is a retry of the real gate, not a bypass. If the underlying problem
  is unresolved (e.g. the vendor is still unlinked, or screening still
  returns `hold`), the payment lands right back on `pending_compliance`
  with a fresh exception opened, rather than being forced through. AP's
  fix — attaching a real vendor, correcting sanctions data, etc. — happens
  out-of-band before calling this.
- **`POST /payments/{id}/compliance/dismiss`** (`payment.void`) — gives up
  without ever reaching the adapter: flips the payment straight to
  `failed` with `failure_reason` set from the required `{reason}` body.
  Use when the payment genuinely should not go out (e.g. the vendor turned
  out to be sanctioned, or is confirmed defunct).

Both are row-locked (`SELECT ... FOR UPDATE`), 409 on any payment not
currently `pending_compliance`, and resolve the invoice's open
`payment_compliance_hold` exception (`resolution` = `"released"` or
`"dismissed: <reason>"`) on success.

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
| `stripe_treasury` | ach, wire | Production. Stripe Treasury for orgs already on Stripe; settles via Treasury FinancialAccount. |
| `increase` | ach, wire, check, rtp | Production. Increase API; same correlation-id idempotency story. |
| `column` | ach, wire | Production. Column.com bank-as-a-service. |
| `dwolla` | ach | Production. ACH-only. Use when the org doesn't want a full Treasury account. |
| `checkeeper` | check | Production. Outsourced check printing + mailing. Pairs with one of the ACH/wire adapters. |

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
 PaymentResult{status, provider_payment_id}    WebhookEvent{provider_payment_id,
        │                                                  status, amount, currency}
        ▼                                          │
 Payment row gets:                                 ▼
   provider, provider_payment_id,            Payment row gets:
   reference, status,                          status (only if not already terminal),
   submitted_at                                reference, failure_reason,
                                               completed_at (on terminal)
                                                   │
                                                   ▼
                                             verify_settlement(...) — see below
```

The orchestrator never auto-completes — only the adapter response (mock) or a webhook (real processor) flips a payment to `completed`. This prevents the platform from claiming money has moved when it hasn't.

#### Settlement-amount verification

A verified HMAC authenticates the **sender**, not the **content**. A signed
`completed` event proves the processor is talking to us about a payment we
know; it does not prove the processor moved the amount on the instruction.
Until this check existed, the handler took the status at face value — it
stamped the regulated `completed_at`, captured any accepted early-pay discount
off *our* authorized number, and handed the run to the ERP sync, with no
comparison against what AP authorized. A wire that left at $50,000 against a
$5,000 instruction, a partial settlement, or a mis-mapped provider integration
all reconciled clean, and the only net was a bank statement someone had to
remember to upload days later.

Identity is not reconciliation. The two reconcilers further downstream already
make exactly this call:

| Where | Signal | Divergent amount becomes |
|---|---|---|
| `positive_pay.classify_presented_items` | cheque found by number | `amount_mismatch` — an ALTERED cheque |
| `bank_reconciliation.classify_discrepancy` | bank line carrying our own trace reference | `amount_mismatch` — linked, excluded from `matched_count` |
| **`payment_settlement.verify_settlement`** | **processor webhook** | **`amount_mismatch` / `currency_mismatch` → `fraud_flag`** |

`services/payment_settlement.py` is pure (no DB, no clock, no I/O) and runs on
every `completed` event. `failed` / `cancelled` events are not verified — no
money moved, so whatever figure they echo reconciles against nothing.

**Two authorized legs, not one.** A cross-currency payment debits
`Payment.source_amount` in the org's home currency and credits `Payment.amount`
in the invoice's currency, and different processors report different sides.
Reporting *either* leg is a match; a third number is not. The target leg's
currency is the invoice's (the `Payment` row doesn't carry one), so the handler
loads the invoice once and reuses it for the discount capture.

| Outcome | When | What the handler does |
|---|---|---|
| `matched` | within one cent of an authorized leg whose currency is compatible | unchanged: capture the discount, hand the run to the ERP sync |
| `amount_mismatch` | a currency-compatible leg exists, none within tolerance | open a `fraud_flag`, skip the discount capture |
| `currency_mismatch` | the reported currency matches no authorized leg | open a `fraud_flag`, skip the discount capture |
| `unverified` | the provider's webhook carried no amount | unchanged, but the blind spot is recorded on the audit row |

Tolerance is one cent — the same band `positive_pay.DEFAULT_AMOUNT_TOLERANCE`
and `bank_reconciliation.AMOUNT_MATCH_TOLERANCE` use. Three reconcilers
disagreeing about what "the same amount" means is how a discrepancy hides in
the gap. `variance` is signed and 2 dp: **positive means the processor moved
MORE than we authorized**, matching `bank_reconciliation.match_variance`.

**What a discrepancy does, and deliberately does not, do.**

- The payment still records as `completed` with its `completed_at`. Money
  moved; refusing to record that does not un-move it, and a payment silently
  parked in `submitted` forever is strictly worse.
- The verdict rides the **same append-only audit row** that records the money
  moving (`details.settlement`), on every completion — matched, mismatched and
  unverified alike. That row is WORM-shipped; the exception row is mutable and
  gets resolved.
- A payment-blocking **`fraud_flag`** opens on the invoice. Deliberately not a
  new exception type: this is the electronic equivalent of the ALTERED cheque
  `api/positive_pay.py` already flags, and `fraud_flag` is in
  `PAYMENT_BLOCKING_EXCEPTION_TYPES`, so the invoice can't be swept into
  another payment run until a human clears it — which is what matters the
  moment this payment is voided and the invoice returns to `approved`. Deduped
  on `(invoice_id, fraud_flag, open|escalated)`, the same rule Positive Pay's
  own return processing uses.
- The **discount capture is skipped**.
  `discount_capture.capture_offers_for_settled_payment` matches an accepted
  offer's discounted payoff against `payment.amount` — our authorized figure,
  which the rail has just contradicted — so capturing would permanently mark
  savings realized on a number that is in dispute and misreport them to the CFO.
- The **invoice is NOT closed out as `paid`** — see below.

##### The invoice waits at `payment_scheduled`

Marking an invoice `paid` is the last irreversible step in the money path:
it's what the ERP, the aging report, the dashboards and the 1099 YTD totals
all read as "settled in full". An **under**-settlement is the case that bites
— the payment row is legitimately `completed` and half the money moved, so
closing the invoice out shorts the vendor with no way to collect the rest
(a `paid` invoice never re-enters a payment run).

The gate lives in `services/payment_erp_sync.py::_sync_payments`, at the
`transition_invoice(..., paid, ...)` call itself rather than at the webhook
that raised the flag. That placement is load-bearing: a run can hold several
payments, and **any** sibling payment's webhook re-dispatches the sync for the
whole run — suppressing the dispatch at the webhook would be leaky, while
gating the transition holds regardless of which webhook triggered it. It
reuses `payment_runs.blocked_invoice_ids`, the same helper `POST /runs` and
`/retry-failed` gate on, so an exception class that blocks money going **out**
also blocks the books being closed on money that already went — the two rules
can't drift.

The payment itself is still pushed to the ERP (it happened); only the
invoice's terminal transition waits. The invoice sits at `payment_scheduled`
with the `fraud_flag` in the exception queue — the same posture the
`erp_reconciliation` exception takes when money may be in flight — until a
human either clears the flag (the next sync for that run marks it paid) or
voids the payment via `POST /api/payments/{id}/void`, which hands the invoice
back to `approved` to be re-paid at the right amount.

**Per-provider coverage.** `WebhookEvent.amount` is in MAJOR units.
Modern Treasury, Stripe, Increase and Column exchange minor units;
`payment_adapters.base.minor_units_to_decimal` is the exact inverse of the
`amount * 100` their own `create_payment` applies, so the round-trip is
symmetric by construction and a currency whose exponent isn't 2 is mis-scaled
identically in both directions rather than producing a phantom mismatch.
Checkeeper and `mock` exchange major-unit decimal strings.

| Provider | Field(s) read | Verified? |
|---|---|---|
| `modern_treasury` | `data.amount` (minor), `data.currency` | yes |
| `stripe_treasury` | `data.object.amount` (minor), `.currency` (lowercase) | yes |
| `increase` | `associated_object.amount` (minor), `.currency` | yes |
| `column` | `data.amount` (minor), `data.currency_code` | yes |
| `checkeeper` | `check.amount` (major string), `check.currency` | yes |
| `mock` | `amount` / `currency` when supplied | yes (local-first) |
| `dwolla` | — | **no** — see below |

Dwolla's event body is a bare `{id, topic, resourceId, _links}` envelope; the
transfer's amount is only reachable by following `_links.resource`, which the
synchronous signature-verification path must not do. Its events therefore read
`unverified` — an honest blind spot recorded on the audit row rather than a
manufactured discrepancy. Closing it means an async re-fetch of the transfer;
until then bank reconciliation remains the downstream net for that rail. The
conversion helpers return `None` (not `Decimal("0")`) for anything
unparseable, precisely so an absent figure can never read as a total
under-settlement.

The reconciler backstop (`services/payment_reconciler.py`) can't verify the
amount: `PaymentAdapter.get_payment_status` returns a bare `PaymentStatus` by
design. A payment settled by the reconciler rather than a webhook therefore
carries no settlement verdict; bank reconciliation is its net.

**Tests:** `tests/test_payment_settlement.py` (the verdict table),
`tests/test_payment_settlement_adapters.py` (per-provider extraction),
`tests/test_payment_settlement_webhook.py` (handler behaviour).

#### Webhook URL

Each tenant configures their processor's webhook URL to:

```
https://app.com/api/payments/webhook/{tenant_slug}/{provider}
```

Tenant is encoded in the path (no `X-Tenant-Slug` header needed — processors don't always support custom headers). The adapter verifies the signature; bad signatures, unknown events, and missing payments all return `204` silently to avoid leaking probing information.

Before any of that, the handler bounds the body against `payment_webhook_max_bytes` (default 4 MiB) — a declared `Content-Length` over the cap rejects without ever awaiting `request.body()`, and the actual read is re-checked in case the header lied or was absent (chunked transfer). The HMAC check happens inside `adapter.parse_webhook`, well after this point, so the cap is what stops an unauthenticated caller from having an arbitrarily large payload buffered fully into memory (memory-exhaustion DoS on a public route) — mirrors `erp_webhook` / `peppol_inbound` / `cards.card_webhook`.

#### Adding a new processor (Stripe Treasury, Increase, Column, …)

1. Copy `mock_adapter.py`, implement the four methods.
2. Register with `@register_payment_adapter("stripe_treasury")`.
3. Map the processor's status enum to `PaymentStatus` in your adapter (Modern Treasury's `_STATUS_MAP` is the reference).
4. Add the provider to the org-settings UI dropdown.
5. Tests: dispatcher returns the new adapter, status-map covers every documented status, webhook signature verification works.

#### Local testing with stripe-mock

The `stripe_treasury` adapter can run offline against Stripe's official API mock —
no `sk_test_` key, no network. It's opt-in under the Compose `payments` profile:

```bash
pnpm stripe:up     # stripe/stripe-mock on :12111
# backend/.env:
#   FEOH_STRIPE_API_BASE=http://localhost:12111/v1
# org settings.payments: provider=stripe_treasury, api_key=sk_test_x, financial_account_id=fa_x
pnpm stripe:down   # stop it
```

`FEOH_STRIPE_API_BASE` (empty = live Stripe) repoints the adapter's API base; a
per-config `api_base` overrides it. stripe-mock returns canned fixtures from
Stripe's OpenAPI spec — it validates request shape + response parsing
(`create_payment`, `get_payment_status`, `test_connection`), not stateful flows
or real webhooks. The seam is locked by `backend/tests/test_stripe_api_base.py`
(CI-safe, no container). For the other processors, the in-process `mock` adapter
remains the local default.

### ERP Payment Sync

After a payment run executes, the system syncs payment data to the connected ERP in a background thread:

```
Execute Payment Run (response sent immediately)
    |
    └── Background thread:
        ├── For each COMPLETED payment in the run:
        │   - Push payment details to ERP (amount, method, reference, date)
        │   - Update invoice status: payment_scheduled → paid
        │   - Log: "[payment-sync] Syncing payment abc: invoice=INV-001, $1,500, method=ach"
        ├── In-flight payments (submitted/processing) are skipped here — they
        │   sync when their terminal-status webhook lands (which re-triggers
        │   the sync for that run).
        |
        └── Log: "[payment-sync] Run xyz: 2 synced, 1 skipped (in-flight), 0 failed"
```

- **Only `completed` payments mark their invoice `paid`.** A run can mix a
  settled `completed` payment with one still `submitted` at the processor;
  marking the in-flight one's invoice `paid` would claim money moved before
  the rail confirmed it (and pre-empt the webhook's own `paid` transition).
  The webhook handler re-dispatches the sync once the in-flight payment settles.
- **An invoice carrying an unresolved payment-blocking exception is HELD** at
  `payment_scheduled` (counted separately from `skipped` in the sweep's log
  line). Reuses `payment_runs.blocked_invoice_ids` — the same
  `PAYMENT_BLOCKING_EXCEPTION_TYPES` tuple run creation and `/retry-failed`
  refuse. The payment is still pushed to the ERP; only the invoice's terminal
  transition waits for the human. See § Settlement-amount verification → The
  invoice waits at `payment_scheduled`.
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
| `POST` | `/api/payments` | Create individual (standalone) payment. The amount is bound server-side to the invoice amount **net of applied credit memos**; `amount` in the body is optional and only a cross-check (422 on disagreement). See § Credit memos are netted on BOTH money paths. |
| `GET` | `/api/payments/runs/` | List payment runs |
| `POST` | `/api/payments/runs` | Create a payment run (draft) |
| `GET` | `/api/payments/runs/{id}` | Get payment run with its payments |
| `POST` | `/api/payments/runs/{id}/execute` | Execute the payment run + trigger ERP sync. `draft`-only — a run stuck `executing` (worker crash mid-run) is resumed via the endpoint below, not this one. |
| `POST` | `/api/payments/runs/{id}/resume` | Resume a run stuck in `executing` — re-dispatches only its still-`pending` payments; anything already `completed`/`failed`/`submitted`/`processing`/`pending_compliance` from before the crash is left untouched. Same `payment.execute` permission gate as `/execute`. |
| `POST` | `/api/payments/runs/{id}/retry-failed` | Re-attempt the safely-retryable FAILED payments of a `partial`/`failed` run by booking a NEW attempt row (the failed row is never mutated). Never re-dispatches a payment that already succeeded, nor one whose fate at the processor is unknown (`needs_reconciliation`); also skips an invoice that is unpayable, carries an unresolved duplicate/fraud/line-total exception, has since been credited, or already has another live payment. Same `payment.execute` gate, segregation check and CFO threshold as `/execute`. See § Why a payment failed, and retrying it. |
| `POST` | `/api/payments/runs/{id}/cancel` | Cancel a draft run — deletes its child payment rows so the invoices return to the queue, and flips the run to `cancelled`. |
| `GET` | `/api/payments/queue` | List invoices ready for payment |
| `GET` | `/api/payments/summary` | KPIs: total paid, pending, queue count, rebates. Requires a `control_db` dependency because `CardRebate` is a control-plane model; the rebate query includes a try/except fallback returning `0.0` if the `card_rebates` table doesn't exist yet. |
| `POST` | `/api/payments/{id}/void` | Void a pending/completed payment. Reverses a `virtual_card` payment's card at the provider too — see § Voiding a card payment cancels the card. |
| `POST` | `/api/payments/{id}/compliance/release` | Re-run compliance-then-adapter for a payment stuck `pending_compliance`. `payment.execute`-gated, 409 outside that status. See § Sanctions / compliance hold resolution. |
| `POST` | `/api/payments/{id}/compliance/dismiss` | Give up on a payment stuck `pending_compliance` — flips it to `failed` with a required `{reason}`, never reaches the adapter. `payment.void`-gated, 409 outside that status. See § Sanctions / compliance hold resolution. |

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
| `GET` | `/api/payments/schedules` | List payment schedules with discount info |
| `PATCH` | `/api/payments/{id}` | Update payment (status, reference) |

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

Every payment **status transition** writes an append-only audit row (project
invariant — the `audit_log` table is DB-level immutable). Executing a run is
the load-bearing money-movement event, so it writes both a run-level row and
one row per child payment recording its terminal state (the transition that
set the regulated `completed_at`). Voiding, CFO sign-off, and cancellation
each write their own row. `details` is PII-free: ids, status, method, the
Decimal `amount` as a string, and the reference — never bank/account values.

| Action | Trigger | Entity |
|---|---|---|
| `payment_run.executed` | A run is executed (`POST /runs/{id}/execute`); rolls up `payments_completed` / `_in_flight` / `_failed` / `cards_issued` + `total_amount` | `payment_run` |
| `payment.completed` | A child payment settled (mock adapter or, in prod, a webhook). A webhook-driven completion also carries `details.settlement` — the settlement-amount verdict (`matched` / `amount_mismatch` / `currency_mismatch` / `unverified`) with the settled + authorized amounts as exact strings and the signed variance. See § Settlement-amount verification | `payment` |
| `payment.failed` | A child payment failed during execution | `payment` |
| `payment.submitted` / `payment.processing` | A child payment is in flight awaiting the processor webhook | `payment` |
| `payment.pending_compliance` | A child payment held by the sanctions/KYC gate | `payment` |
| `payment.compliance_released` | `POST /{id}/compliance/release` re-ran compliance-then-adapter for a held payment | `payment` |
| `payment.compliance_dismissed` | `POST /{id}/compliance/dismiss` gave up on a held payment, flipping it to `failed` | `payment` |
| `payment.voided` | A completed / in-flight payment voided (`POST /{id}/void`); also writes the `invoice.voided_return_to_approved` invoice row | `payment` |
| `payment_run.cfo_approved` | CFO sign-off on an over-threshold draft run | `payment_run` |
| `payment_run.cancelled` | A draft run cancelled before execution | `payment_run` |

The invoice side of a payment is audited separately by
`transition_invoice` (`invoice.payment_scheduled` on execute,
`invoice.voided_return_to_approved` on void) against the `invoice` entity.

## Role Access

| Action | Admin | AP Manager | AP Clerk | CFO |
|---|---|---|---|---|
| View payment queue | Yes | Yes | No | Yes |
| Create payment run | Yes | Yes | No | No |
| Execute payment run | Yes | No | No | Yes |
| View payment history | Yes | Yes | No | Yes |
| Void a payment | Yes | No | No | Yes |

CFO approval is required for executing payment runs above a configurable
threshold (`Organization.settings.payments.cfo_approval_above`, a `Decimal`).
The gate is **strict `>`**, matching the setting name (`cfo_approval_above`): a
run whose total is *above* the threshold requires CFO sign-off; a run exactly
*at* the threshold does not, and a threshold of `0` (or negative) disables the
gate entirely. A configured-but-unparseable threshold fails **closed** — the run
is created `requires_cfo_approval=True` and the misconfiguration is logged
(PII-free) for an admin to correct, rather than silently disabling the control.

`PaymentRun.total_amount` is a single bare `Numeric` column with no currency
of its own, so `create_payment_run_for_invoices` refuses (422) a batch whose
invoices don't all share one currency — summing a USD and a EUR invoice into
one total would misfire (or fail to fire) this gate on a face-value
coincidence across currencies. Each `Payment` still settles independently in
its own invoice's currency at execution time; this only constrains what one
run can report a single total for.

### Financial-integrity exception gate

Independently of the CFO threshold, `create_payment_run` refuses any invoice
that still carries an **unresolved** (`open`/`escalated`) exception of a class
listed in `payments.PAYMENT_BLOCKING_EXCEPTION_TYPES`. This gate (along with
the payable-status check, credit-memo netting, and the CFO-threshold
computation above) lives in `services/payment_runs.py::create_payment_run_for_invoices`
— `POST /api/payments/runs` and the AI Cash-Flow Copilot's
`POST /api/cash-flow/plans/{plan_id}/draft-run` (`docs/cash-flow-copilot.md`
§5/§6) both call the same function, so a fraud-control gate can never diverge
between the manual and copilot-driven paths:

| Type | What it would let through |
|------|---------------------------|
| `duplicate` | the same invoice approved and paid twice |
| `fraud_flag` | a bank-detail swap, rush payment, statistical anomaly, an altered / never-issued cheque from a Positive Pay return, or a processor settlement that didn't reconcile against what AP authorized (§ Settlement-amount verification) |
| `line_total_mismatch` | a header `amount` that openly disagrees with the invoice's own line items — the run pays the header, and the header is never silently recomputed from the lines (see `line-total-reconciliation.md`) |

Each is raised as an `error`-severity advisory flag, and **approval does not gate
on any of them** — nothing in `services/review.py` or `workflow_engine.py` reads
warning severity, so all three can be approved straight past. Payment-run
creation is the gate that stops the money.

Resolving or dismissing the exception is the human sign-off that clears it and
makes the invoice payable again; `escalated` still blocks, because it means a
human is still working it. The run is refused as a whole, naming only the
offending invoices, so the operator drops or clears them rather than guessing.

Coverage: `tests/test_payment_run_blocking_exceptions.py` drives real exception
rows against a real DB, so the membership of the tuple is pinned in **both**
directions (a `po_mismatch`, which is advisory here, must not block).

## Code Structure

```
backend/app/api/payments.py                # All payment endpoints (CRUD, runs, queue, summary)
backend/app/models/payment.py              # Payment, PaymentRun, PaymentSchedule models
backend/app/schemas/payment.py             # Pydantic schemas
backend/app/services/payment_runs.py       # Shared run-creation validation + rollups
backend/app/services/payment_settlement.py # Pure settlement-amount verifier (webhook)
backend/app/services/payment_reconciler.py # Backstop polling for missing webhooks
backend/app/services/payment_erp_sync.py   # Async ERP sync after payment execution
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
