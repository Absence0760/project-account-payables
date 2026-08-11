"""Recognize a settled payment as the realization of an accepted early-pay
discount — the missing caller for ``discount_offers.mark_captured``.

``services/discount_offers.mark_captured`` is the only code that sets
``DiscountOffer.captured_amount`` / ``captured_at`` and transitions an offer
``accepted -> captured``, but it is a pure mutator: something has to notice
"this payment settling this invoice IS the discounted payoff" and call it.
This module is that something. It is invoked from every place a ``Payment``
reaches ``completed`` against an invoice — the synchronous adapter/card leg
in ``app/api/payments.py::_execute_single_payment`` and the async
webhook-driven completion in ``app/api/payments.py::payment_webhook`` — so a
discount is recognized whether the rail confirms instantly (mock, virtual
card) or days later (ACH/wire via a processor webhook).

Money is ``Decimal`` throughout; this module does its own DB query but never
commits — same convention as the rest of the payment path (the caller owns
the transaction).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.discount import OFFER_SCOPE_INVOICE, OFFER_STATUS_ACCEPTED, DiscountOffer
from app.services import discount_offers as offers_svc

logger = logging.getLogger(__name__)

_CENTS = Decimal("0.01")


async def capture_offers_for_settled_payment(
    db: AsyncSession,
    *,
    invoice_id: uuid.UUID,
    payment_amount: Decimal,
    now: datetime,
) -> list[DiscountOffer]:
    """Capture any ``accepted`` invoice-scoped ``DiscountOffer`` on
    ``invoice_id`` whose accepted tier's discounted payoff exactly matches
    ``payment_amount``.

    Only invoice-scoped offers are considered (``scope == "invoice"``) — a
    vendor-scoped bulk offer's ``base_amount`` is the summed open balance
    across several invoices, so no single invoice's payment can be proven to
    BE that offer's settlement; guessing would misattribute savings, so those
    are left ``accepted`` for a future reconciliation pass rather than
    auto-captured here.

    The match is an EXACT cent comparison — ``payment_amount`` against
    ``offer.base_amount - discount_savings(base_amount, accepted_tier)``,
    both cent-quantized the same way — not a tolerance band. A payment for
    the full (undiscounted) amount, or for any other amount, leaves the
    offer ``accepted`` rather than being guessed at; a false "captured"
    would misreport realized savings to the CFO dashboard just as badly as
    the missing-caller bug this closes.

    Idempotent: only offers currently ``accepted`` are queried, so calling
    this again for the same settlement (a retry, a reconciliation re-run)
    finds nothing left to capture — never double-counts, never raises on an
    already-``captured`` offer. The ``mark_captured`` ``ValueError`` is also
    caught defensively in case a concurrent settlement path captured the same
    offer between the query and the mutation.

    Returns the offers captured (0 in the common no-discount case, 1 when a
    discounted payoff was recognized; a list because nothing stops more than
    one ``accepted`` offer existing on the same invoice at once).
    """
    result = await db.execute(
        select(DiscountOffer).where(
            DiscountOffer.invoice_id == invoice_id,
            DiscountOffer.scope == OFFER_SCOPE_INVOICE,
            DiscountOffer.status == OFFER_STATUS_ACCEPTED,
        )
    )
    offers = result.scalars().all()
    if not offers:
        return []

    paid = Decimal(payment_amount).quantize(_CENTS)
    captured: list[DiscountOffer] = []
    for offer in offers:
        if not offer.accepted_tier:
            continue
        savings = offers_svc.discount_savings(offer.base_amount, offer.accepted_tier)
        discounted_payoff = (offer.base_amount - savings).quantize(_CENTS)
        if paid != discounted_payoff:
            continue
        try:
            offers_svc.mark_captured(offer, captured_amount=savings, now=now)
        except ValueError:
            # Lost a race with another settlement/reconciliation path that
            # captured this same offer first — already handled, no-op.
            logger.info(
                "discount offer %s already captured; skipping duplicate capture",
                offer.id,
            )
            continue
        captured.append(offer)
    return captured
