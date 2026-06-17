"""Supplier-financing (supply-chain-finance) adapter contract.

A supply-chain-finance (SCF) marketplace funds a supplier's early
payment: the financier pays the supplier *now* (the `advance_amount`,
which is the invoice face value minus a financing `fee_amount`), and
the buyer repays the financier at the invoice's original `due_date`.
The adapter's two jobs:

  1. `quote(...)` — price the early-payment offer for one invoice /
     vendor (eligibility, discount/fee, funding + repayment dates,
     net advance the supplier would receive).
  2. `request_funding(...)` — accept a quote and actually fund it.
     Idempotent at the API boundary (it moves money toward the
     supplier), so the caller passes an `idempotency_key`.

All money and percent fields are `Decimal` (project invariant — never
`float` for money). Config comes from
`Organization.settings.financing` — see the dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class FinancingQuote:
    """An early-payment offer for a single invoice.

    `eligible` is the load-bearing field: when `False` the supplier /
    buyer cannot finance this invoice (e.g. provider declined, missing
    credentials, term too short) and `reason` carries a PII-free
    explanation. The money fields are zeroed on an ineligible quote.

    `discount_percent` is the early-payment discount expressed as a
    percent of the invoice face value; `fee_percent` is the financier's
    fee as a percent of the face value (the two are the same economic
    quantity from opposite sides of the table — the financier's fee is
    the buyer/supplier's discount). `advance_amount` is what the
    supplier receives now: invoice face value minus the fee.

    `funding_date` is when the financier disburses to the supplier
    (≈ today); `repayment_date` is when the buyer repays the financier
    (the invoice's original `due_date`).
    """

    provider: str
    eligible: bool
    discount_percent: Decimal
    fee_percent: Decimal
    funding_date: date | None
    repayment_date: date | None
    advance_amount: Decimal
    reason: str | None = None
    # Preserved so an auditor can replay the provider's raw response.
    raw_response: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FinancingFundingResult:
    """Outcome of a `request_funding` call.

    `funded` True means the financier accepted and disbursed (or
    queued) the advance; `external_funding_id` is the provider's handle
    for the funded position (used for reconciliation and as the
    idempotency anchor on retries). `status` mirrors the provider's
    lifecycle state (e.g. `"funded"`, `"pending"`, `"declined"`).
    `reason` carries a PII-free explanation when `funded` is False.
    """

    provider: str
    funded: bool
    external_funding_id: str | None
    advance_amount: Decimal
    fee_amount: Decimal
    status: str
    reason: str | None = None
    raw_response: dict = field(default_factory=dict)


class FinancingAdapter(Protocol):
    """The minimum contract every supplier-financing provider satisfies."""

    provider_name: str

    async def quote(
        self,
        *,
        invoice_amount: Decimal,
        currency: str,
        due_date: date,
        vendor_name: str,
        vendor_country: str | None = None,
    ) -> FinancingQuote:
        """Price an early-payment offer for one invoice.

        `invoice_amount` is the face value; `due_date` is the invoice's
        original payment-due date (the financier's repayment date).
        Implementations return an ineligible `FinancingQuote` rather
        than raising when the provider simply declines — a missing
        credential is the one case that may fail closed (raise).
        """
        ...

    async def request_funding(
        self,
        *,
        quote: FinancingQuote,
        idempotency_key: str,
    ) -> FinancingFundingResult:
        """Accept a previously-issued `quote` and fund it.

        Idempotent on `idempotency_key`: the same key must always map
        to the same `external_funding_id` so a retried request never
        double-funds (project invariant — writes that move money are
        idempotent).
        """
        ...

    async def test_connection(self) -> bool:
        """Cheapest available probe (auth check). True on success."""
        ...
