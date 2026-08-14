"""Pydantic schemas for bank reconciliation.

Shared contract for the ``/api/bank-reconciliation`` router. Money fields use
the ``MoneyAmount`` annotations (Decimal in Python, JSON number on the wire);
IDs are strings on the wire, parsed to UUID in the router. See
``backend/docs/bank-reconciliation.md``.
"""

from __future__ import annotations

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
    # The matched payment's own amount, so a reviewer can see both sides of an
    # `amount_mismatch` without a second request.
    matched_payment_amount: OptionalMoneyAmount = None
    # Signed gap between what the bank moved and what the payment authorises
    # (`services.bank_reconciliation.match_variance`). POSITIVE means the bank
    # took MORE than we authorised — the direction that matters for fraud.
    # NULL when the transaction is unmatched.
    variance_amount: OptionalMoneyAmount = None
    # False for an `amount_mismatch` line: linked to a payment, but the amounts
    # disagree, so it has NOT cleared. Mirrors
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
    # RECONCILED lines only — an `amount_mismatch` is linked but not cleared,
    # and counting it here would report the discrepancy as resolved.
    matched_count: int
    # Debits linked to a payment whose amount disagrees. Computed on read from
    # the transactions themselves — no stored column, no migration.
    amount_mismatch_count: int = 0
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
    an existing (correct or incorrect) match back to unmatched."""

    matched_payment_id: str | None = Field(default=None)


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


class AmountMismatchResponse(BaseModel):
    """A bank debit identified as one of our payments that moved a DIFFERENT
    amount. The fraud/error bucket."""

    transaction_id: str
    statement_id: str
    account_identifier: str
    transaction_date: str
    bank_amount: MoneyAmount
    payment_amount: MoneyAmount
    # Positive = the bank took MORE than we authorised.
    variance_amount: MoneyAmount
    payment_id: str
    invoice_number: str | None = None
    counterparty_name: str | None = None


class OutstandingItemsResponse(BaseModel):
    """Org-wide reconciliation state, computed on read across every imported
    statement — the period-close view the per-statement detail can't give."""

    as_of: str
    older_than_days: int
    uncleared_payments: list[UnclearedPaymentResponse]
    uncleared_count: int
    uncleared_total: MoneyAmount
    unmatched_debits: list[UnmatchedDebitResponse]
    unmatched_debit_count: int
    unmatched_debit_total: MoneyAmount
    amount_mismatches: list[AmountMismatchResponse]
    amount_mismatch_count: int
    # Signed sum of every variance. Positive = the bank has taken more than we
    # authorised in aggregate.
    amount_mismatch_net_variance: MoneyAmount
