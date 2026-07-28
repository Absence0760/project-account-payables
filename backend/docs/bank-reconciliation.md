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
| `POST` | `/{id}/transactions/{tx_id}/resolve` | Manually set (`matched_payment_id: "<uuid>"`) or clear (`null`) a transaction's match — `match_method="manual"`, confidence 100 when set. Recomputes the statement's `matched_count`. |
| `DELETE` | `/{id}` | Delete a statement; cascades its transactions. |

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
"skipped_credit": K}`.

Three strategies, in order:

| Order | Strategy | Method | Confidence | When |
|---|---|---|---|---|
| 1 | Exact ID | `provider_id` | 100 | Transaction `reference` matches `Payment.provider_payment_id` or `Payment.reference` |
| 2 | Amount + date | `amount_date` | 80 | Exactly one candidate Payment in the ±N-day window has the same amount |
| 3 | Fuzzy vendor | `fuzzy_vendor` | 50–70 | Multiple candidates, disambiguated by Jaccard similarity between transaction's `counterparty_name` and the invoice's `vendor_name` (≥ 0.5 floor) |

Credits are skipped entirely — they're not payments we made.

When strategies 2 and 3 both fail (multiple candidates, no fuzzy
hit), the transaction stays unmatched. Better to leave unmatched
than to credit the wrong invoice — the AP team triages from the
exceptions queue.

## Match confidence semantics

| Range | Treatment |
|---|---|
| 100 | Auto-mark payment as reconciled; no review needed |
| 80–99 | Auto-mark; AP can audit-trail later |
| 50–79 | Show on the review queue with "looks like" wording |
| < 50 (never returned today) | Reserved for future ML signals |

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
| `tests/test_bank_reconciliation.py` | CSV sniffing (signed-amount + separate debit/credit; parenthesized negatives; comma + dollar-sign amounts; reference + counterparty pass-through; bad rows skipped); refusal paths (empty / header-only / missing columns / all-bad-rows / latin-1 fallback); matcher strategies (provider_id 100, amount_date 80, fuzzy_vendor 50–70, multi-candidate-no-fuzzy unmatched, credits skipped); outcome-count rollup |
| `tests/test_bank_reconciliation_api.py` | The HTTP surface end-to-end against real test tenants: upload → persisted statement + matched transactions + audit row, credits skipped, malformed CSV → 422, list + detail (transactions omitted from list), manual resolve (set + clear, audited, `matched_count` recomputed), resolve against an unknown payment → 404, delete cascades transactions, RBAC (`ap_clerk` reads but can't upload/delete) |
