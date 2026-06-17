"""Mock supplier-financing adapter — deterministic for tests and local dev.

The safe local-first default: no network, no credential, no randomness.
Every output is a pure function of the inputs, so a test (or a repeated
`pnpm dev` run) sees the same quote and the same funding id every time.

Quote formula
-------------
The financier's fee is a simple time-value-of-money charge proportional
to how long the buyer's payment is being accelerated:

    days_to_due  = max(0, repayment_date - funding_date)   # in days
    fee_percent  = annual_rate * days_to_due / 365         # as a percent
    fee_amount   = invoice_amount * fee_percent / 100      # rounded to cents
    advance_amount = invoice_amount - fee_amount

`annual_rate` defaults to 6.0 (≈ 6% APR) and is overridable via
`financing_config["mock_annual_rate_percent"]`. The early-payment
*discount* the buyer/supplier sees equals the fee expressed as a
percent of face value, so `discount_percent == fee_percent`.

`funding_date` is "today" (overridable via
`financing_config["mock_funding_date"]` as an ISO date string so tests
are deterministic regardless of the wall clock); `repayment_date` is
the invoice's original `due_date`.

Eligibility
-----------
Ineligible (zeroed money, `reason` set) when the invoice amount is
non-positive or the due date is on/before the funding date (nothing to
accelerate). A test can force ineligibility for a vendor name via
`financing_config["mock_ineligible_vendors"]`.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.services.financing_adapters.base import FinancingFundingResult, FinancingQuote
from app.services.financing_adapters.dispatcher import register_financing_adapter

_CENTS = Decimal("0.01")
_PCT = Decimal("0.0001")  # 4dp on the percent so the math is exact
_DEFAULT_ANNUAL_RATE_PERCENT = Decimal("6.0")
_ZERO = Decimal("0.00")


@register_financing_adapter("mock")
class MockFinancingAdapter:
    provider_name = "mock"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        rate = self.config.get("mock_annual_rate_percent")
        self.annual_rate_percent: Decimal = (
            Decimal(str(rate)) if rate is not None else _DEFAULT_ANNUAL_RATE_PERCENT
        )
        funding = self.config.get("mock_funding_date")
        self._funding_date_override: date | None = (
            date.fromisoformat(funding) if funding else None
        )
        ineligible = self.config.get("mock_ineligible_vendors") or []
        self._ineligible_vendors = {v.strip().lower() for v in ineligible}

    def _funding_date(self) -> date:
        return self._funding_date_override or datetime.now(UTC).date()

    async def quote(
        self,
        *,
        invoice_amount: Decimal,
        currency: str,
        due_date: date,
        vendor_name: str,
        vendor_country: str | None = None,
    ) -> FinancingQuote:
        funding_date = self._funding_date()
        amount = Decimal(invoice_amount)

        def _ineligible(reason: str) -> FinancingQuote:
            return FinancingQuote(
                provider=self.provider_name,
                eligible=False,
                discount_percent=_ZERO,
                fee_percent=_ZERO,
                funding_date=funding_date,
                repayment_date=due_date,
                advance_amount=_ZERO,
                reason=reason,
                raw_response={"reason": reason},
            )

        if amount <= 0:
            return _ineligible("invoice_amount_non_positive")
        if (vendor_name or "").strip().lower() in self._ineligible_vendors:
            return _ineligible("vendor_not_eligible")

        days_to_due = (due_date - funding_date).days
        if days_to_due <= 0:
            # Nothing to accelerate — invoice is due now or overdue.
            return _ineligible("no_acceleration_window")

        fee_percent = (
            self.annual_rate_percent * Decimal(days_to_due) / Decimal(365)
        ).quantize(_PCT, rounding=ROUND_HALF_UP)
        fee_amount = (amount * fee_percent / Decimal(100)).quantize(
            _CENTS, rounding=ROUND_HALF_UP
        )
        advance_amount = (amount - fee_amount).quantize(_CENTS, rounding=ROUND_HALF_UP)

        return FinancingQuote(
            provider=self.provider_name,
            eligible=True,
            discount_percent=fee_percent,
            fee_percent=fee_percent,
            funding_date=funding_date,
            repayment_date=due_date,
            advance_amount=advance_amount,
            reason=None,
            raw_response={
                "currency": currency,
                "days_to_due": days_to_due,
                "annual_rate_percent": str(self.annual_rate_percent),
                "fee_amount": str(fee_amount),
            },
        )

    async def request_funding(
        self,
        *,
        quote: FinancingQuote,
        idempotency_key: str,
    ) -> FinancingFundingResult:
        if not quote.eligible:
            return FinancingFundingResult(
                provider=self.provider_name,
                funded=False,
                external_funding_id=None,
                advance_amount=_ZERO,
                fee_amount=_ZERO,
                status="declined",
                reason=quote.reason or "quote_ineligible",
                raw_response={"idempotency_key": idempotency_key},
            )

        # Deterministic external id derived purely from the
        # idempotency key — same key in, same id out, no randomness, no
        # clock. A retried request therefore reconciles to one funded
        # position (idempotency invariant).
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
        external_funding_id = f"mock-fund-{digest}"

        # Recover the fee from the quote alone (it carries no face
        # value): advance = face - fee and fee = face * pct/100, so
        # fee = advance * pct / (100 - pct). Exact when pct < 100, which
        # always holds for a real financing fee.
        pct = quote.fee_percent
        denom = Decimal(100) - pct
        fee_amount = (
            (quote.advance_amount * pct / denom).quantize(_CENTS, rounding=ROUND_HALF_UP)
            if denom > 0
            else _ZERO
        )

        return FinancingFundingResult(
            provider=self.provider_name,
            funded=True,
            external_funding_id=external_funding_id,
            advance_amount=quote.advance_amount,
            fee_amount=fee_amount,
            status="funded",
            reason=None,
            raw_response={
                "idempotency_key": idempotency_key,
                "repayment_date": quote.repayment_date.isoformat()
                if quote.repayment_date
                else None,
            },
        )

    async def test_connection(self) -> bool:
        return True
