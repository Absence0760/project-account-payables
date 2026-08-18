"""Recording a settlement against a payment — the DB-touching half.

`services/payment_settlement.py` is pure: it decides a verdict and says what
`payments.settled_amount` can hold. This module is what WRITES that verdict —
fetching a missing figure from the processor, persisting the settled columns,
and raising the payment-blocking exception a discrepancy warrants.

It exists because there are **two** paths on which a payment reaches
`completed`, and they had drifted. `payment_webhook` verified the settlement,
put the verdict on the append-only audit row and opened a `fraud_flag` on a
mismatch. The reconciler backstop — the path that runs precisely when the
webhook never arrived, i.e. the case with the LEAST evidence — persisted the
figure and stopped there: no verdict, no audit block, no exception. A rail
reporting a 10x overpayment settled silently and `settlement_coverage` marked
the invoice `paid`, because over-settlement is `covered` by design. A short
settlement stranded the invoice at `payment_scheduled` with nothing in the
queue to explain it. And because the webhook handler refuses a payment that is
already terminal, a late webhook could never supply the missing verdict.

One implementation, both callers, so the two cannot disagree about what
"verified" means.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exception import Exception as APException
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.payment import Payment
from app.services.payment_settlement import (
    SettlementVerification,
    describe_discrepancy,
    persistable_settled_amount,
    verify_settlement,
)

logger = logging.getLogger(__name__)


async def record_settlement(
    db: AsyncSession,
    *,
    payment: Payment,
    adapter,
    invoice: Invoice | None,
    reported_amount: Decimal | None = None,
    reported_currency: str | None = None,
) -> SettlementVerification:
    """Verify what the rail says it settled and persist it onto ``payment``.

    ``reported_amount``/``reported_currency`` are what the caller already
    holds — the webhook event's own figures. When the amount is absent (a rail
    like Dwolla sends a bare ``{id, topic, resourceId}`` envelope, and
    ``get_payment_status`` returns a bare status by design) the processor is
    asked directly via the optional ``fetch_settlement`` capability.

    Best-effort on every axis: an adapter without the capability reports
    ``available=False``, and ANY failure leaves the verdict `unverified`
    rather than breaking the money path — a settlement fetch must never break
    the webhook that is recording money movement, nor halt the sweep's tick.

    Returns the verdict for the caller to put on its audit row. Does not
    commit.
    """
    if reported_amount is None and getattr(payment, "provider_payment_id", None):
        try:
            report = await adapter.fetch_settlement(payment.provider_payment_id)
        except Exception as exc:  # noqa: BLE001 - best-effort by contract
            # Class only, never the message — a processor SDK error string can
            # embed partial account data (PII-out-of-logs invariant).
            logger.warning(
                "settlement fetch failed for payment=%s: %s",
                payment.id,
                exc.__class__.__name__,
            )
        else:
            if report.available and report.amount is not None:
                reported_amount = report.amount
                reported_currency = report.currency

    verification = verify_settlement(
        reported_amount=reported_amount,
        reported_currency=reported_currency,
        target_amount=payment.amount,
        target_currency=(invoice.currency if invoice is not None else None),
        source_amount=payment.source_amount,
        source_currency=payment.source_currency,
    )

    # Persist what the rail says it moved, beside what AP authorized. A rail
    # that reported nothing leaves the columns NULL rather than writing a zero:
    # NULL means "no figure on record" and fails OPEN in the coverage check,
    # while a 0 would read as a total shortfall and hold every such invoice
    # forever. A figure too wide for NUMERIC(15, 2) is recorded as the
    # `settled_amount_unstorable` flag — assigning it would raise at the flush
    # and roll back the whole transaction, which on the webhook path means the
    # processor retries into the same failure forever and on the sweep path
    # aborts the entire tick.
    storable, unstorable = persistable_settled_amount(verification.settled_amount)
    if storable is not None:
        payment.settled_amount = storable
        payment.settled_currency = verification.settled_currency
    if unstorable:
        payment.settled_amount_unstorable = True
        payment.settled_currency = verification.settled_currency

    return verification


async def open_settlement_mismatch_exception(
    db: AsyncSession,
    *,
    payment: Payment,
    invoice: Invoice | None,
    org: Organization,
    verification: SettlementVerification,
) -> None:
    """Raise a payment-blocking `fraud_flag` for a settlement that didn't
    reconcile against what AP authorized.

    `fraud_flag` deliberately, not a new taxonomy entry: it is exactly what
    `api/positive_pay.py` raises for an ALTERED cheque — a payment instrument
    presented at an amount we never wrote — and this is the electronic
    equivalent, caught days earlier on rails where no cheque exists. It is
    also in `PAYMENT_BLOCKING_EXCEPTION_TYPES`, so until a human resolves it
    the invoice can't be swept into another payment run (which matters the
    moment this payment is voided and the invoice returns to `approved`).

    Dedupes on `(invoice_id, fraud_flag, open|escalated)` — the same rule
    Positive Pay's own return processing uses. One open fraud flag on an
    invoice is the signal; piling on a second adds noise, not information.
    The description is PII-free (amounts, currency codes and the verdict —
    the row already carries the invoice FK).
    """
    if invoice is None:
        # A payment whose invoice row is gone still gets the audit row; there
        # is no queue entry to attach the flag to.
        return
    from app.services.exception_service import create_exception

    already = (
        await db.execute(
            select(func.count()).where(
                APException.invoice_id == invoice.id,
                APException.exception_type == "fraud_flag",
                APException.status.in_(["open", "escalated"]),
            )
        )
    ).scalar() or 0
    if already > 0:
        return

    await create_exception(
        db,
        exception_type="fraud_flag",
        description=describe_discrepancy(verification),
        organization_id=org.id,
        severity="error",
        invoice=invoice,
    )


__all__ = ["record_settlement", "open_settlement_mismatch_exception"]
