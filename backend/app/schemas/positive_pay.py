"""Pydantic schemas for Positive Pay / payment-fraud files.

Shared contract for the ``/api/positive-pay`` router and the frontend
``/positive-pay`` route. Money fields use the ``MoneyAmount`` /
``OptionalMoneyAmount`` annotations (Decimal in Python, JSON number on the
wire); IDs are strings on the wire, parsed to UUID in the router.

PII discipline: no full account / routing number ever appears in these
schemas. ``account_last4`` is the only account detail exposed on a response;
``PresentedItemIn`` carries only a check number + amount. See
``backend/docs/positive-pay.md``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.money import MoneyAmount, OptionalMoneyAmount


class GenerateCheckIssueRequest(BaseModel):
    """Generate a check-issue Positive Pay file for a payment run."""

    bank_format: str = Field(default="csv", max_length=30)


class GenerateAchAuthorizationRequest(BaseModel):
    """Generate a standalone ACH debit-authorization file for the org."""

    bank_format: str = Field(default="csv", max_length=30)


class PresentedItemIn(BaseModel):
    """One item the bank reports as presented for payment, for return
    processing. Only a check number + amount — never an account number."""

    check_number: str | None = Field(default=None, max_length=100)
    amount: OptionalMoneyAmount = None


class ProcessReturnRequest(BaseModel):
    """The bank's return: the items it saw presented against an issued file."""

    presented_items: list[PresentedItemIn] = Field(default_factory=list)


class PositivePayFileResponse(BaseModel):
    id: str
    file_type: str
    bank_format: str
    status: str
    payment_run_id: str | None
    item_count: int
    total_amount: MoneyAmount
    account_last4: str | None
    file_key: str | None
    created_at: str
    updated_at: str | None
    meta: dict | None = None


class PositivePayListResponse(BaseModel):
    items: list[PositivePayFileResponse]
    total: int
    page: int
    page_size: int


class ProcessReturnResponse(BaseModel):
    presented_count: int
    matched_ok: int
    amount_mismatches: int
    not_on_file: int
    exceptions_created: int
    file: PositivePayFileResponse
