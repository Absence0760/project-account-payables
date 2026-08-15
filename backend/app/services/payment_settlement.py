"""Settlement-amount verification — did the processor move what we authorized?

The processor's webhook is the settlement moment for every real rail: ACH,
wire, SEPA and cheque all sit ``submitted``/``processing`` until the provider
calls back, and that callback is what stamps the regulated ``completed_at``.
Until now the handler took the provider's word for the *status* and never
looked at the *amount* — a webhook saying "settled" flipped the payment to
``completed``, captured any accepted early-pay discount and told the ERP the
invoice was paid, without one comparison against the figure AP actually
authorized.

That is the same hole the other two reconcilers already close, one and two
steps further downstream:

* ``positive_pay.classify_presented_items`` — a cheque found by number whose
  presented amount differs beyond a cent is ``amount_mismatch`` (an ALTERED
  cheque), never ``matched_ok``.
* ``bank_reconciliation.classify_discrepancy`` — a bank line matched on our
  own trace reference whose amount differs is ``amount_mismatch``, linked but
  excluded from ``matched_count``.

Identity is not reconciliation. A ``provider_payment_id`` proves *which*
payment the event is about; it does not prove the processor moved the amount
on the instruction. This module supplies the missing comparison at the
earliest, most authoritative point — days before a bank statement is uploaded,
and for rails (ACH, SEPA, wire) where no cheque ever exists to present.

Pure: no DB, no clock, no I/O. ``app/api/payments.py::payment_webhook`` owns
what to *do* with a discrepancy (audit row + a payment-blocking ``fraud_flag``
exception + suppressing the discount capture); this module only decides
whether there is one.

Two legs are authorized, not one. A cross-currency payment debits
``Payment.source_amount`` in the org's home currency and credits
``Payment.amount`` in the invoice's currency (see
``services/international_payments.prepare_international_payment``), and
different processors report different sides of that. Reporting *either*
authorized leg is a match; reporting a third number is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# One cent — the same band `positive_pay.DEFAULT_AMOUNT_TOLERANCE` uses for its
# altered-cheque call and `bank_reconciliation.AMOUNT_MATCH_TOLERANCE` uses for
# its statement match. Kept identical on purpose: three reconcilers disagreeing
# about what "the same amount" means is how a discrepancy hides in the gap.
SETTLEMENT_AMOUNT_TOLERANCE = Decimal("0.01")

_CENTS = Decimal("0.01")

OUTCOME_MATCHED = "matched"
OUTCOME_AMOUNT_MISMATCH = "amount_mismatch"
OUTCOME_CURRENCY_MISMATCH = "currency_mismatch"
OUTCOME_UNVERIFIED = "unverified"

#: Outcomes that mean the settlement did NOT reconcile against an authorized
#: leg. ``unverified`` is deliberately absent — "the provider told us nothing"
#: is not evidence of a discrepancy, and treating it as one would open a
#: ``fraud_flag`` on every payment from an adapter that omits the field.
DISCREPANCY_OUTCOMES = frozenset({OUTCOME_AMOUNT_MISMATCH, OUTCOME_CURRENCY_MISMATCH})

REASON_NO_REPORTED_AMOUNT = "provider_reported_no_amount"
REASON_CURRENCY_NOT_AUTHORIZED = "settled_currency_not_authorized"
REASON_AMOUNT_DIFFERS = "settled_amount_differs_from_authorization"


@dataclass(frozen=True)
class AuthorizedLeg:
    """One side of what AP authorized: an exact amount and its currency.

    ``currency`` is ``None`` when the caller genuinely cannot establish it
    (an invoice row that has since been deleted). A ``None`` currency is a
    wildcard for the currency check — we never manufacture a currency
    mismatch out of missing data — while the amount comparison still runs.
    """

    amount: Decimal
    currency: str | None
    leg: str  # "target" (invoice currency) | "source" (org home currency)


@dataclass(frozen=True)
class SettlementVerification:
    """Verdict on one reported settlement.

    ``variance`` is signed and 2dp: ``reported - authorized``, so **positive
    means the processor moved MORE than we authorized** — the same sign
    convention ``bank_reconciliation.match_variance`` uses for a bank debit.
    It is ``None`` whenever no numeric comparison was possible (nothing
    reported, or the reported currency matched no authorized leg).
    """

    outcome: str
    reason: str | None = None
    variance: Decimal | None = None
    settled_amount: Decimal | None = None
    settled_currency: str | None = None
    authorized_amount: Decimal | None = None
    authorized_currency: str | None = None
    authorized_leg: str | None = None

    @property
    def is_discrepancy(self) -> bool:
        return self.outcome in DISCREPANCY_OUTCOMES

    def as_details(self) -> dict:
        """PII-free dict for an ``audit_log.details`` payload.

        Money serialises as an exact decimal STRING, never a float (project
        invariant). Nothing here names a vendor, a bank account or a tax id —
        only amounts, currency codes and the verdict.
        """
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "settled_amount": (None if self.settled_amount is None else str(self.settled_amount)),
            "settled_currency": self.settled_currency,
            "authorized_amount": (
                None if self.authorized_amount is None else str(self.authorized_amount)
            ),
            "authorized_currency": self.authorized_currency,
            "authorized_leg": self.authorized_leg,
            "variance": None if self.variance is None else str(self.variance),
        }


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _same_currency(a: str | None, b: str | None) -> bool:
    """A ``None`` on either side is a wildcard — see ``AuthorizedLeg``."""
    if a is None or b is None:
        return True
    return a.strip().upper() == b.strip().upper()


def build_authorized_legs(
    *,
    target_amount: Decimal,
    target_currency: str | None,
    source_amount: Decimal | None = None,
    source_currency: str | None = None,
) -> list[AuthorizedLeg]:
    """The set of amounts a processor may legitimately report for a payment.

    The target leg (``Payment.amount`` in the invoice's currency) is always
    present. The source leg (``Payment.source_amount`` in the org's home
    currency) is added only for a payment that actually carries one — i.e. one
    that went through the international/FX path. When the two legs are the
    same amount in the same currency (a domestic payment where
    ``prepare_international_payment`` still stamped the home currency) the
    duplicate is dropped so the verdict names one leg, not an arbitrary one
    of two identical ones.
    """
    legs = [AuthorizedLeg(amount=_q(target_amount), currency=target_currency, leg="target")]
    if source_amount is not None:
        source = AuthorizedLeg(amount=_q(source_amount), currency=source_currency, leg="source")
        if not (
            source.amount == legs[0].amount and _same_currency(source.currency, legs[0].currency)
        ):
            legs.append(source)
    return legs


def verify_settlement(
    *,
    reported_amount: Decimal | None,
    reported_currency: str | None,
    target_amount: Decimal,
    target_currency: str | None,
    source_amount: Decimal | None = None,
    source_currency: str | None = None,
    tolerance: Decimal = SETTLEMENT_AMOUNT_TOLERANCE,
) -> SettlementVerification:
    """Compare what the processor says it settled against what AP authorized.

    Outcomes:

    ``matched``
        The reported amount is within ``tolerance`` of an authorized leg
        whose currency the report is compatible with. The ordinary path.

    ``amount_mismatch``
        A currency-compatible leg exists but no leg's amount is within
        tolerance. ``authorized_*`` names the CLOSEST such leg and
        ``variance`` is the signed gap against it.

    ``currency_mismatch``
        The processor reported a currency that matches no authorized leg.
        No numeric comparison is meaningful across currencies, so
        ``variance`` stays ``None`` and ``authorized_*`` reports the target
        leg for context.

    ``unverified``
        The provider's webhook carried no amount at all (an adapter whose
        payload genuinely omits it). Fails OPEN by design: an absent field
        is not evidence, and inventing a discrepancy from it would flag every
        payment on that rail. The caller records the outcome on the audit row
        so the blind spot is visible rather than silent, and bank
        reconciliation remains the downstream net.
    """
    legs = build_authorized_legs(
        target_amount=target_amount,
        target_currency=target_currency,
        source_amount=source_amount,
        source_currency=source_currency,
    )
    target_leg = legs[0]

    if reported_amount is None:
        return SettlementVerification(
            outcome=OUTCOME_UNVERIFIED,
            reason=REASON_NO_REPORTED_AMOUNT,
            settled_currency=(reported_currency or None),
            authorized_amount=target_leg.amount,
            authorized_currency=target_leg.currency,
            authorized_leg=target_leg.leg,
        )

    settled = _q(reported_amount)
    candidates = [leg for leg in legs if _same_currency(reported_currency, leg.currency)]

    if not candidates:
        return SettlementVerification(
            outcome=OUTCOME_CURRENCY_MISMATCH,
            reason=REASON_CURRENCY_NOT_AUTHORIZED,
            settled_amount=settled,
            settled_currency=reported_currency,
            authorized_amount=target_leg.amount,
            authorized_currency=target_leg.currency,
            authorized_leg=target_leg.leg,
        )

    for leg in candidates:
        if abs(settled - leg.amount) <= tolerance:
            return SettlementVerification(
                outcome=OUTCOME_MATCHED,
                variance=_q(settled - leg.amount),
                settled_amount=settled,
                settled_currency=reported_currency,
                authorized_amount=leg.amount,
                authorized_currency=leg.currency,
                authorized_leg=leg.leg,
            )

    closest = min(candidates, key=lambda leg: abs(settled - leg.amount))
    return SettlementVerification(
        outcome=OUTCOME_AMOUNT_MISMATCH,
        reason=REASON_AMOUNT_DIFFERS,
        variance=_q(settled - closest.amount),
        settled_amount=settled,
        settled_currency=reported_currency,
        authorized_amount=closest.amount,
        authorized_currency=closest.currency,
        authorized_leg=closest.leg,
    )


def describe_discrepancy(verification: SettlementVerification) -> str:
    """One-line, PII-free summary for an ``Exception.description``.

    Names amounts, currencies and the verdict — never a vendor, a bank
    account or an invoice number (the exception row already carries the
    invoice FK).
    """
    settled = (
        f"{verification.settled_amount} {verification.settled_currency or '?'}"
        if verification.settled_amount is not None
        else "an unreported amount"
    )
    authorized = (
        f"{verification.authorized_amount} {verification.authorized_currency or '?'}"
        if verification.authorized_amount is not None
        else "the authorized amount"
    )
    if verification.outcome == OUTCOME_CURRENCY_MISMATCH:
        return (
            f"Settlement currency mismatch: the processor reported {settled} but this "
            f"payment authorizes {authorized}. Money moved on a currency AP did not "
            f"authorize — reconcile with the processor before releasing this invoice."
        )
    variance = verification.variance
    direction = "MORE than" if (variance or Decimal("0")) > 0 else "LESS than"
    return (
        f"Settlement amount mismatch: the processor reported {settled}, "
        f"{direction} the authorized {authorized} (variance {variance}). "
        f"Money moved at an amount AP did not authorize — reconcile with the "
        f"processor before releasing this invoice."
    )
