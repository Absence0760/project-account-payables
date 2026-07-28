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
    matched_count: int
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
