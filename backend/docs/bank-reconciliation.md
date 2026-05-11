# Bank Reconciliation

Import a bank statement (CSV today; OFX / camt.053 reserved) and
auto-match the debit transactions against the `Payment` rows that
should appear on it. Match results are surfaced to the AP team:
matched rows show their match method + confidence; unmatched rows
become exceptions.

## Components

| Layer | File | Purpose |
|---|---|---|
| Models | `app/models/bank_reconciliation.py` | `BankStatement`, `BankTransaction` |
| Migration | `alembic/versions/0019_bank_reconciliation.py` | Tables + indexes (tenant DB) |
| Importer | `app/services/bank_reconciliation.py::parse_csv_statement` | CSV → rows |
| Matcher | `app/services/bank_reconciliation.py::match_statement_transactions` | Rows → `Payment` |

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
