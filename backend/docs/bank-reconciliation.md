# Bank Reconciliation

Import a bank statement (CSV today; OFX / camt.053 reserved) and
auto-match the debit transactions against the `Payment` rows that
should appear on it. Match results are surfaced to the AP team:
matched rows show their match method + confidence; unmatched rows
stay visible on the statement detail view for manual review.

## Components

| Layer | File | Purpose |
|---|---|---|
| Models | `app/models/bank_reconciliation.py` | `BankStatement`, `BankTransaction` |
| Migration | `alembic/versions/0019_bank_reconciliation.py` | Tables + indexes (tenant DB) |
| Importer | `app/services/bank_reconciliation.py::parse_csv_statement` | CSV → rows |
| Matcher | `app/services/bank_reconciliation.py::match_statement_transactions` | Rows → `Payment` |
| API | `app/api/bank_reconciliation.py` | `/api/bank-reconciliation` — the HTTP surface below |

## API (`/api/bank-reconciliation`)

Not entity-scoped (predates multi-entity; a bank account is org-wide, not
per-subsidiary — mirrors how an unscoped `GLAccount` is shared). Read
admin/ap_manager/ap_clerk/cfo; mutate admin/ap_manager only (treasury-adjacent
raw account data, same write gate as Positive Pay — clerks excluded). Every
mutation writes a PII-free audit row (`bank_reconciliation.imported` /
`.transaction_resolved` / `.deleted`).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/upload` | Multipart CSV upload (`file`, `account_identifier`, `period_start`, `period_end`, `currency`). Parses via `parse_csv_statement`, persists the statement + transactions, runs `match_statement_transactions`, returns the detail view. 422 on a malformed CSV (`StatementImportError`). |
| `GET` | `` | List statements, paginated, optional `?account_identifier=` filter. |
| `GET` | `/{id}` | Statement detail including every transaction (list omits transactions to avoid an N+1 payload on the index). |
| `GET` | `/outstanding` | Org-wide close view: uncleared payments, unmatched bank debits, and amount mismatches. `?older_than_days=` (default 0) / `?limit=` (default 200). See below. |
| `POST` | `/{id}/transactions/{tx_id}/resolve` | Manually set (`matched_payment_id: "<uuid>"`) or clear (`null`) a transaction's match — confidence 100 when set; `match_method` is `manual`, or `amount_mismatch` when the amounts disagree (see § Identity is not reconciliation). Recomputes the statement's `matched_count`. |
| `DELETE` | `/{id}` | Delete a statement; cascades its transactions. |

`/outstanding` is declared **before** `/{statement_id}` in the router —
FastAPI matches in declaration order and a `uuid.UUID` path param would 422
on the literal rather than falling through.

Raw-file storage (the uploaded CSV → S3, for audit replay) is deferred —
`file_key` is always `NULL` today, matching `vendor_statement_recon`'s CSV
intake.

## Deferred

- **`unmatched_bank_transaction` exception.** Today an unmatched transaction
  is only visible by opening the statement detail view; it does not raise an
  `Exception` row the way `invoice_warnings.py` does for duplicates/fraud, so
  it won't surface on the shared exceptions queue. Durable fix: after
  `match_statement_transactions` runs (in the `/upload` handler), open a
  de-duped `unmatched_bank_transaction` Exception per still-unmatched debit
  (mirroring `positive_pay`'s invoice-less `fraud_flag` pattern for a
  non-invoice-linked exception), and clear/resolve it when the transaction is
  later matched via `/resolve`. Needs an `Exception` row with no
  `invoice_id` — already supported since migration 0049. **Open design
  question before implementing**: `BankStatement`/`BankTransaction` carry no
  `entity_id` (org-wide, predates multi-entity — see above), but
  `api/exceptions.py`'s list query calls `apply_entity_scope(...)` WITHOUT
  `include_shared=True`, so an `Exception` row stamped `entity_id=None` would
  be invisible to any user with a specific entity selected — only visible in
  the "all entities" consolidated view. Positive Pay's invoice-less
  `fraud_flag` sidesteps this because Positive Pay files ARE entity-scoped
  (`entity_id` comes from a real `get_write_entity_id`), so its exceptions
  always get a real entity, never `None`. Bank reconciliation has no entity to
  attribute to, so this needs a real decision (stamp the org's default entity?
  add `include_shared=True` to the exceptions query? something else?) — not a
  default worth guessing at silently, since guessing wrong ships a queue item
  most multi-entity tenants would never see.
- **Frontend page.** No `/bank-reconciliation` route ships yet in the SPA;
  the API is usable today via any HTTP client / the `/docs` Swagger UI. A
  dedicated page (statement list, upload form, transaction match-review
  table) is tracked as its own follow-up — same shape as `/vendor-statements`.
- **OFX / camt.053 import.** `source_format` already carries the value;
  only the CSV parser is implemented.

## CSV importer

`parse_csv_statement(raw_csv, organization_id, account_identifier,
period_start, period_end, currency="USD", imported_by=None,
file_key=None)` parses a CSV bytes blob into a `BankStatement`
plus a list of unsaved `BankTransaction` rows. Encoding sniff
(UTF-8 with BOM → latin-1 fallback) so non-ASCII bank exports
don't crash.

Column sniffing — the parser accepts any of these synonyms (case +
whitespace insensitive):

| Field | Accepted column names |
|---|---|
| Date | `Date`, `Transaction Date`, `Transaction_Date`, `Posted Date`, `Post Date` |
| Amount (signed) | `Amount`, `Transaction Amount`, `Amount (USD)`, `Value` |
| Debit (separate) | `Debit`, `Withdrawal`, `Withdrawals` |
| Credit (separate) | `Credit`, `Deposit`, `Deposits` |
| Description | `Description`, `Memo`, `Details`, `Note`, `Narrative` |
| Reference | `Reference`, `Ref`, `Trace Number`, `Trace`, `Check Number` |
| Counterparty | `Counterparty`, `Payee`, `Name`, `Merchant`, `Vendor` |

Amount parser handles:
- `1234.56`, `1,234.56`, `$1,234.56`
- `-1234.56` (negative as debit)
- `(1,234.56)` (Quickbooks-style parenthesized negative)

Date parser tries ISO (`%Y-%m-%d`) first, then US (`%m/%d/%Y`), then
DMY (`%d/%m/%Y`). Ambiguous dates like `01/02/2026` are read as
US — operators should export ISO when possible.

Raises `StatementImportError` on:
- empty CSV
- header row only, no data
- header missing a date column
- header missing both signed-amount AND debit/credit columns
- every data row unparseable (bad header config)

Skips silently (with a WARNING log) any individual row whose date
or amount is unparseable.

## Matcher

`match_statement_transactions(db, transactions, window_days=5)`
mutates every `direction == "debit"` transaction in place, setting
`matched_payment_id`, `match_method`, `match_confidence`, and
`matched_at`. Returns counts: `{"matched": N, "unmatched": M,
"skipped_credit": K, "amount_mismatch": V}`.

Three strategies, in order:

| Order | Strategy | Method | Confidence | When |
|---|---|---|---|---|
| 1 | Exact ID | `provider_id` | 100 | Transaction `reference` matches `Payment.provider_payment_id` or `Payment.reference` **and the amounts agree** |
| 2 | Amount + date | `amount_date` | 80 | Exactly one candidate Payment in the ±N-day window has the same amount |
| 3 | Fuzzy vendor | `fuzzy_vendor` | 50–70 | Multiple candidates, disambiguated by Jaccard similarity between transaction's `counterparty_name` and the invoice's `vendor_name` (≥ 0.5 floor) |

Credits are skipped entirely — they're not payments we made.

When strategies 2 and 3 both fail (multiple candidates, no fuzzy
hit), the transaction stays unmatched. Better to leave unmatched
than to credit the wrong invoice — the AP team triages from the
exceptions queue.

### Ambiguous references

Neither lookup column is unique. `Payment.reference` is free text a caller
supplies on `POST /api/payments`, and the virtual-card path stamps a derived
`CARD-<provider>-<last4>` that collapses to `CARD-LITHIC-????` whenever the
last-four is unknown. A reference that names **more than one** payment proves
nothing, so strategy 1 treats it as no match and falls through to 2/3 rather
than picking one arbitrarily. (It previously used `scalar_one_or_none()`,
which raised `MultipleResultsFound` and 500'd the entire import — every other
line on the file lost with it.)

## Identity is not reconciliation — the `amount_mismatch` class

Strategy 1 matches on a reference string, so it can identify a payment while
the bank moved a **different amount** than we authorised: a wire that left at
$50,000 against a $5,000 instruction, an altered cheque, a duplicated fee.
That is the single discrepancy bank reconciliation exists to catch, and it
used to come back as `provider_id` / confidence 100 / *matched*.

Such a line is now classified `amount_mismatch`:

- It stays **linked** to its payment (`matched_payment_id` set), so the
  discrepancy is traceable and no other transaction can claim that payment.
- It is **not reconciled**. `BankStatement.matched_count` counts only lines
  passing `services.bank_reconciliation.is_reconciled` — the single predicate
  the matcher, the manual `/resolve` recompute and the outstanding-items
  report all share.
- `match_variance(bank_amount, payment_amount)` gives the signed 2dp `Decimal`
  gap. **Positive means the bank took MORE than we authorised.** It is
  computed on read from the two amounts already stored — no column, no
  migration, nothing to drift.
- The tolerance is one cent (`AMOUNT_MATCH_TOLERANCE`), the same band
  `positive_pay.classify_presented_items` uses for its altered-cheque call.

Strategies 2 and 3 key off an exact amount, so only strategy 1 can produce
one. This gives bank reconciliation the `amount_mismatch` classification the
other two reconcilers already have (`positive_pay` for cheques presented to
the bank, `vendor_statement_recon` for a supplier's statement of open items).

**The manual path is classified the same way.** `POST
.../transactions/{id}/resolve` derives `match_method` from the amounts rather
than trusting the caller: a human pointing a bank line at a payment is
supplying an identity the matcher could not infer, not asserting the amounts
agree. A clerk therefore cannot click a $10 line into place as the clean
clearing of a $10,000 payment — it lands `amount_mismatch`, and the audit row
records the exact variance they accepted.

### One payment, one bank transaction

A `Payment` may be claimed by at most one `BankTransaction` — two bank lines
cannot both be "the" clearing of a single payment without double-counting it
as reconciled. The automatic matcher enforces it with its `claimed` set
(seeded from every prior statement's matches, then grown within the batch);
`/resolve` enforces it with an explicit check.

That check is a read-then-write, so `/resolve` **row-locks the payment**
(`SELECT ... FOR UPDATE`) before running it — mirroring the money-path
convention in `api/payments.py`, where `/approve`, `/execute`, `/cancel` and
`/void` all lock the row they gate on. Without the lock, two concurrent
resolves pointing *different* transactions at the *same* payment both read
"not claimed", both passed, and both committed. Pinned by
`test_concurrent_resolve_cannot_claim_one_payment_twice`.

The invariant has no unique index behind it, so existing data may already hold
more than one claimant; the check reads with `LIMIT 1` rather than asking for
exactly-one, which would 500 on precisely the rows it exists to reject. A
partial unique index on `matched_payment_id` is the durable backstop — tracked
in [followups.md](../../docs/followups.md), since it needs a migration and a
decision about what to do with any pre-existing duplicates.

## Match confidence semantics

Confidence scores how sure we are of the **identity** — which payment this
line is. Whether it *reconciled* is a separate question, answered by
`is_reconciled` / the `amount_mismatch` class above, and a confidence-100
line can still be unreconciled.

| Range | Treatment |
|---|---|
| 100 | Certain identity — an exact reference hit, or a human's manual resolve |
| 80–99 | Single amount+date candidate in the window |
| 50–79 | Fuzzy vendor-name disambiguation; review the "looks like" wording |
| < 50 (never returned today) | Reserved for future ML signals |

## Outstanding items (`GET /outstanding`)

Per-statement detail answers *"did this file reconcile"*. Nothing answered
*"across everything we have imported, what has still not cleared"* — the
question month-end actually asks — so reconciliation state was unreadable
outside one statement at a time. `/outstanding` is the three-bucket bank-rec
worksheet, computed on read across every imported statement:

| Bucket | Meaning |
|---|---|
| `uncleared_payments` | Our books say it went out; no bank line claims it. Payments in `completed` / `submitted` / `processing` only — `pending` was never dispatched, `failed`/`cancelled`/`voided` are terminal non-payments, and `pending_compliance` is held *before* the adapter call. |
| `unmatched_debits` | Money left the account with no payment behind it — the never-issued-cheque shape. |
| `amount_mismatches` | Identified, but the bank moved a different amount. Carries both sides plus the signed `variance_amount`. |

`?older_than_days=N` (default 0) reports only payments sent at least N days
ago — a payment submitted this morning is not yet outstanding. Age is measured
off `submitted_at` → `completed_at` → `created_at`, the same fallback chain
the matcher's date window uses, so "outstanding since" and "matchable around"
agree on when we consider a payment sent.

A payment linked to an `amount_mismatch` line is **not** uncleared: it is
accounted for in the mismatch bucket, so it appears exactly once.

`?limit=` caps the returned rows only — every count and total covers the full
set, so a truncated page never understates the money. Each bucket therefore
runs **two** queries: a SQL aggregate (`COUNT` + `SUM`, exact `Decimal`) over
the whole set, and a separate `LIMIT`-ed row fetch. The age filter is SQL too
(`COALESCE(submitted_at, completed_at, created_at)::date <= cutoff`), so
nothing unbounded is loaded into memory — a month-end close on a large
unreconciled backlog is the exact shape this endpoint has to survive. A row
with no usable timestamp at all is surfaced rather than hidden behind a filter
it could never satisfy.

There is **no stored clearance column** on `Payment`. Clearance is derived
from the existing `BankTransaction.matched_payment_id` link, so voiding a
payment or re-pointing a match cannot leave a denormalised flag asserting
something the transactions no longer support. (This mirrors how
`budget_service` computes spend on read rather than keeping a running total.)

## Migration

`0019_bank_reconciliation.py` (tenant DB only):

- `bank_statements` — id, organization_id, account_identifier,
  currency, period_start, period_end, source_format,
  file_key, imported_by, opening_balance, closing_balance,
  transaction_count, matched_count, imported_at.
- `bank_transactions` — id, statement_id (FK CASCADE),
  organization_id, transaction_date, posted_date, amount, currency,
  description, counterparty_name, reference, direction (debit |
  credit), raw_data JSONB, matched_payment_id (FK SET NULL),
  match_method, match_confidence, matched_at.
- Indexes: `(statement_id, transaction_date)`,
  partial `(matched_payment_id)`, `(organization_id, transaction_date DESC)`.

## Tests

| File | Coverage |
|---|---|
| `tests/test_bank_reconciliation.py` | CSV sniffing (signed-amount + separate debit/credit; parenthesized negatives; comma + dollar-sign amounts; reference + counterparty pass-through; bad rows skipped); refusal paths (empty / header-only / missing columns / all-bad-rows / latin-1 fallback); matcher strategies (provider_id 100, amount_date 80, fuzzy_vendor 50–70, multi-candidate-no-fuzzy unmatched, credits skipped); outcome-count rollup; the `amount_mismatch` class (`match_variance` signed + exact, one-cent tolerance, `is_reconciled` excludes it, a reference hit at the wrong amount is linked-not-matched and still claims the payment); ambiguous references on both lookup columns fall through instead of crashing |
| `tests/test_bank_reconciliation_api.py` | The HTTP surface end-to-end against real test tenants: upload → persisted statement + matched transactions + audit row, credits skipped, malformed CSV → 422, list + detail (transactions omitted from list), manual resolve (set + clear, audited, `matched_count` recomputed), resolve against an unknown payment → 404, delete cascades transactions, RBAC (`ap_clerk` reads but can't upload/delete); `amount_mismatch` end-to-end (upload flags it, list surfaces `amount_mismatch_count`, manual resolve can't stamp a wrong amount as reconciled and audits the variance as an exact string, a duplicated `Payment.reference` no longer 500s the import); `/outstanding` (all three buckets, `older_than_days` filter, a cleanly reconciled payment drops out, no double-reporting of a mismatched payment, `?limit` truncates rows but never the counts/totals); two concurrent resolves can't both claim one payment |
