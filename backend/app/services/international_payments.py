"""International payments orchestration.

Glues together the FX adapter, corridor selector, and banking
validators so `execute_payment_run` (and the explicit
`/api/payments` endpoints) can submit a cross-currency or
cross-border payment without re-implementing the bookkeeping every
time.

Two responsibilities:

  1. `prepare_international_payment` — given an invoice + vendor +
     org FX config, pick the corridor, lock the FX rate, populate
     the Payment row's source/target/fx_* fields. Returns a Payment
     instance (uncommitted — caller flushes).

  2. `compute_fx_gain_loss` — when a foreign-currency invoice
     settles, the rate has usually moved since the invoice booked.
     The difference between the booked accrual and the cash paid
     out is a realized FX gain/loss; finance teams report on it.

Both functions are pure-ish (no DB calls inside them); the caller
hands the rate adapter in. That keeps the orchestration testable
without spinning up an httpx client.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.models.payment import Payment
from app.services.fx_adapters import FXAdapter, FXRate
from app.services.payment_corridor import CorridorChoice, pick_corridor
from app.utils.banking import (
    country_from_iban,
    is_sepa_country,
    validate_iban,
    validate_swift_bic,
)


class InternationalPaymentError(ValueError):
    """Raised when a payment can't be prepared — bad bank fields,
    unsupported corridor, etc. The orchestrator surfaces this; the
    caller turns it into an HTTPException with the right status."""


@dataclass(frozen=True)
class PreparedPayment:
    """A Payment row plus the corridor and FX evidence it was built
    from. We return both so callers (and tests) can assert on the
    decision the orchestrator made without re-deriving it."""

    payment: Payment
    corridor: CorridorChoice
    fx_rate: FXRate | None


_MONEY_QUANT = Decimal("0.01")


def _quantize_money(value: Decimal) -> Decimal:
    """Round to currency precision (2 dp, banker's rounding off in
    favor of `ROUND_HALF_UP` — matches what auditors expect)."""
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


async def prepare_international_payment(
    *,
    invoice,
    vendor,
    org_home_currency: str,
    fx_adapter: FXAdapter,
    invoice_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    requested_method: str | None = None,
) -> PreparedPayment:
    """Build a Payment row for a (potentially) international invoice.

    Picks the corridor, fetches and locks the FX rate, applies the
    rate to compute the source-side outflow in the org's home
    currency, and validates the vendor's bank fields for the chosen
    corridor.

    `invoice` and `vendor` are duck-typed (SimpleNamespace in tests,
    SQLAlchemy ORM in production). We read:

      - `invoice.id`, `invoice.amount`, `invoice.currency`,
        `invoice.correlation_id`, `invoice.organization_id`
      - `vendor.bank_details` (JSONB dict)
      - `vendor.address_country` (optional; falls back to IBAN
        country, then `None`)

    Raises `InternationalPaymentError` on any structural problem.
    """
    target_currency = (invoice.currency or org_home_currency).upper()
    source_currency = org_home_currency.upper()

    bank = vendor.bank_details or {}
    iban = bank.get("iban") or ""
    swift = bank.get("swift_bic") or bank.get("swift") or bank.get("bic") or ""
    target_country = (
        bank.get("country")
        or getattr(vendor, "address_country", None)
        or country_from_iban(iban)
    )
    if isinstance(target_country, str):
        target_country = target_country.upper()

    corridor = pick_corridor(
        source_currency=source_currency,
        target_currency=target_currency,
        target_country=target_country,
        requested_method=requested_method,
    )

    # Structural validation for the corridor's required fields. We
    # check what's needed BEFORE doing the rate lookup — a network
    # call to OXR is wasted if we can't submit anyway.
    if corridor.requires_iban:
        if not validate_iban(iban):
            raise InternationalPaymentError(
                f"corridor '{corridor.method}' requires a valid IBAN; "
                f"vendor's bank_details.iban is missing or malformed"
            )
    if corridor.requires_swift:
        if not validate_swift_bic(swift):
            raise InternationalPaymentError(
                f"corridor '{corridor.method}' requires a valid SWIFT/BIC; "
                f"vendor's bank_details.swift_bic is missing or malformed"
            )

    # FX lookup. Skipped when the corridor doesn't need a conversion
    # (domestic / same-currency).
    fx_rate: FXRate | None = None
    source_amount = invoice.amount
    fx_rate_decimal: Decimal | None = None
    fx_locked_at: datetime | None = None

    if corridor.requires_fx:
        fx_rate = await fx_adapter.get_rate(source_currency, target_currency)
        if fx_rate.rate <= 0:
            raise InternationalPaymentError(
                f"FX provider returned non-positive rate for "
                f"{source_currency}→{target_currency}: {fx_rate.rate}"
            )
        # invoice.amount is in target_currency; source_amount = target / rate
        # e.g. invoice = 1000 EUR, USD→EUR = 0.92 → source = 1000 / 0.92 USD
        source_amount = _quantize_money(invoice.amount / fx_rate.rate)
        fx_rate_decimal = fx_rate.rate
        fx_locked_at = fx_rate.as_of

    payment = Payment(
        invoice_id=invoice_id or invoice.id,
        correlation_id=correlation_id or invoice.correlation_id,
        amount=invoice.amount,                # paid in invoice currency
        method=corridor.method,
        status="pending",
        source_currency=source_currency,
        source_amount=source_amount,
        fx_rate=fx_rate_decimal,
        fx_locked_at=fx_locked_at,
        corridor=corridor.method,
        target_country=target_country,
    )

    return PreparedPayment(payment=payment, corridor=corridor, fx_rate=fx_rate)


def compute_fx_gain_loss(
    *,
    invoice_amount: Decimal,
    invoice_currency: str,
    paid_source_amount: Decimal,
    paid_source_currency: str,
    fx_rate_at_invoice: Decimal,
    fx_rate_at_payment: Decimal,
) -> Decimal:
    """Compute the realized FX gain/loss for a foreign-currency
    invoice that has now been paid.

    Booking flow:
      - At invoice approval we accrue a liability of
        `invoice_amount / fx_rate_at_invoice` in the home currency.
      - At payment we actually move `paid_source_amount` (in the
        home currency).
      - Gain (positive) = paid less than accrued.
        Loss (negative) = paid more than accrued.

    Same currency → 0 regardless of rate inputs (defensive against
    a caller passing a stale rate by mistake).
    """
    if invoice_currency.upper() == paid_source_currency.upper():
        return Decimal("0.00")
    if fx_rate_at_invoice <= 0:
        raise ValueError("fx_rate_at_invoice must be positive")
    accrued = _quantize_money(invoice_amount / fx_rate_at_invoice)
    realized = _quantize_money(paid_source_amount)
    # Positive when paid_source_amount < accrued — we paid less than
    # we booked → gain. Sign convention matches GAAP / IFRS.
    return _quantize_money(accrued - realized)


def is_international_payment(payment: Payment) -> bool:
    """Convenience predicate used by reporting / payment_erp_sync.

    A payment is international iff:
      - It has an explicit FX rate locked (cross-currency), OR
      - Its corridor is one of the international rails (`sepa`,
        `international_wire`).
    """
    if payment.fx_rate is not None and payment.fx_rate > 0:
        return True
    if payment.corridor in ("sepa", "international_wire"):
        return True
    return False
