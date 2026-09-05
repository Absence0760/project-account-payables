"""Pydantic schemas for bank reconciliation.

Shared contract for the ``/api/bank-reconciliation`` router. Money fields use
the ``MoneyAmount`` annotations (Decimal in Python, JSON number on the wire);
IDs are strings on the wire, parsed to UUID in the router. See
``backend/docs/bank-reconciliation.md``.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.schemas.money import MoneyAmount, OptionalMoneyAmount


class BankTransactionResponse(BaseModel):
    id: str
    transaction_date: str
    posted_date: str | None
    amount: MoneyAmount
    currency: str
    description: str | None
    counterparty_name: str | None
    reference: str | None
    direction: str
    matched_payment_id: str | None
    matched_invoice_number: str | None = None
    match_method: str | None
    # A 0-100 confidence score, not a currency amount — plain float, not the
    # Decimal-exact MoneyAmount annotation.
    match_confidence: float | None = None
    matched_at: str | None
    # What the bank account was debited for the matched payment — the FX leg's
    # home-currency figure for an international payment, `Payment.amount`
    # otherwise (`services.bank_reconciliation.settlement_amount_and_currency`).
    # Lets a reviewer see both sides of a discrepancy without a second request.
    matched_payment_amount: OptionalMoneyAmount = None
    # The currency that settlement amount is denominated in. NULL when it can't
    # be established — which is also when the currency comparison is skipped.
    matched_payment_currency: str | None = None
    # The matched payment's own status — what makes a `status_conflict` row
    # readable ("the bank moved money against a payment we call `failed`").
    matched_payment_status: str | None = None
    # Signed gap between what the bank moved and what the payment authorises
    # (`services.bank_reconciliation.match_variance`). POSITIVE means the bank
    # took MORE than we authorised — the direction that matters for fraud.
    # NULL when the transaction is unmatched, or when the two sides are in
    # different currencies (subtracting across currencies isn't money).
    variance_amount: OptionalMoneyAmount = None
    # False for any discrepancy line (`amount_mismatch` / `currency_mismatch` /
    # `status_conflict`): linked to a payment, but it has NOT cleared. Mirrors
    # `services.bank_reconciliation.is_reconciled`.
    is_reconciled: bool = False


class BankStatementResponse(BaseModel):
    id: str
    account_identifier: str
    currency: str
    period_start: str
    period_end: str
    source_format: str
    file_key: str | None
    opening_balance: OptionalMoneyAmount = None
    closing_balance: OptionalMoneyAmount = None
    transaction_count: int
    # RECONCILED lines only — a discrepancy line is linked but not cleared,
    # and counting it here would report the discrepancy as resolved.
    matched_count: int
    # Debits linked to a payment whose amount disagrees. Computed on read from
    # the transactions themselves — no stored column, no migration.
    amount_mismatch_count: int = 0
    # Every linked-but-unreconciled line: the amount mismatches above PLUS
    # `currency_mismatch` and `status_conflict`. The single "something on this
    # statement needs a human" number.
    discrepancy_count: int = 0
    imported_at: str
    created_at: str
    # Transactions are included on the detail response only (list omits them).
    transactions: list[BankTransactionResponse] | None = None


class BankStatementListResponse(BaseModel):
    items: list[BankStatementResponse]
    total: int
    page: int
    page_size: int


class TransactionResolveRequest(BaseModel):
    """Manually set or clear a transaction's matched payment. ``None`` clears
    an existing (correct or incorrect) match back to unmatched.

    Typed ``uuid.UUID``, not ``str``: the router used to call ``uuid.UUID(...)``
    on the raw value with no handler, so a malformed id was a 500 rather than
    the 422 a validation failure owes the caller.
    """

    matched_payment_id: uuid.UUID | None = Field(default=None)


# --------------------------------------------------------------------------- #
# Outstanding items (`GET /api/bank-reconciliation/outstanding`)
# --------------------------------------------------------------------------- #


class UnclearedPaymentResponse(BaseModel):
    """A payment our books say went out that no bank line has claimed — the
    "outstanding cheques / payments in transit" side of a bank rec worksheet."""

    payment_id: str
    invoice_id: str
    invoice_number: str | None = None
    vendor_name: str | None = None
    amount: MoneyAmount
    # The currency `amount` is denominated in — the invoice's, since
    # `Payment.amount` is invoice-currency. `unmatched_debits` and
    # `discrepancies` both carried one; without it this bucket rendered in the
    # org's reporting currency and a multi-currency tenant saw the wrong symbol.
    currency: str | None = None
    method: str | None = None
    status: str
    # `submitted_at` → `completed_at` → `created_at`, the same fallback chain
    # the matcher's date window uses.
    sent_on: str | None = None
    days_outstanding: int | None = None


class UnmatchedDebitResponse(BaseModel):
    """A bank debit with no payment behind it — money left the account that we
    have no record of authorising."""

    transaction_id: str
    statement_id: str
    account_identifier: str
    transaction_date: str
    amount: MoneyAmount
    currency: str
    counterparty_name: str | None = None
    reference: str | None = None
    description: str | None = None


class DiscrepancyResponse(BaseModel):
    """A bank debit we identified as one of our payments that does NOT
    reconcile. The fraud/error bucket — `classification` says how it fails."""

    transaction_id: str
    statement_id: str
    account_identifier: str
    transaction_date: str
    # `amount_mismatch` | `currency_mismatch` | `status_conflict`
    # (`services.bank_reconciliation.UNRECONCILED_MATCH_METHODS`).
    classification: str
    bank_amount: MoneyAmount
    bank_currency: str
    # What the bank account was debited for the payment — the FX leg's
    # home-currency figure when it has one, `Payment.amount` otherwise.
    payment_amount: MoneyAmount
    payment_currency: str | None = None
    # Our books' own status for the payment. A `status_conflict` row is one
    # where this is NOT a dispatched status.
    payment_status: str | None = None
    # Positive = the bank took MORE than we authorised. Set for the
    # `amount_mismatch` class only: a cross-currency gap isn't money, and a
    # `status_conflict` agrees on the amount by definition.
    variance_amount: OptionalMoneyAmount = None
    payment_id: str
    invoice_number: str | None = None
    counterparty_name: str | None = None


class BankReconCurrencyTotal(BaseModel):
    """One currency's slice of an outstanding-items total.

    ``total`` is an EXACT decimal string, never a float — a money figure a user
    reads, matching every other whole-set rollup in this codebase."""

    currency: str
    total: str


class OutstandingItemsResponse(BaseModel):
    """Org-wide reconciliation state, computed on read across every imported
    statement — the period-close view the per-statement detail can't give."""

    as_of: str
    older_than_days: int
    uncleared_payments: list[UnclearedPaymentResponse]
    uncleared_count: int
    # Grouped per currency, NEVER a cross-currency SUM. `Payment.amount` is
    # invoice-currency, so totalling it across a multi-currency tenant produced
    # a figure denominated in nothing real — the same rule
    # `amount_mismatch_net_variance` below already states for subtraction.
    uncleared_totals: list[BankReconCurrencyTotal]
    unmatched_debits: list[UnmatchedDebitResponse]
    unmatched_debit_count: int
    # Same rule: a statement carries its own currency, and a tenant can import
    # statements for accounts in different ones.
    unmatched_debit_totals: list[BankReconCurrencyTotal]
    # Every identified-but-unreconciled line, whatever its class.
    discrepancies: list[DiscrepancyResponse]
    discrepancy_count: int
    # Signed sum of the AMOUNT-mismatch subset's variances. Positive = the bank
    # has taken more than we authorised in aggregate. Deliberately not summed
    # over the other classes: a cross-currency subtraction isn't money.
    amount_mismatch_net_variance: MoneyAmount
