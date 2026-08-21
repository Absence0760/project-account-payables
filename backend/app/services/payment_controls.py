"""Human-authorization controls on the payment money path.

Two controls live here, both about *who* may commit an outflow rather than
*whether* the outflow is arithmetically sound:

* **Maker-checker** (`check_run_segregation`) — identity-level segregation of
  duties on a payment run.
* **The CFO-approval threshold** (`cfo_approval_decision`) — the money-size gate
  above which a second, more senior human must sign off.

Identity-level maker-checker for payment runs: the user who CREATED a run must
not be the one who CFO-approves it or EXECUTES it (the actual money movement).

This is orthogonal to the role/permission split (`require_permission`): that
separates duties by ROLE, but a single user holding a role with both
`payment_run.approve` and `payment_execute` — the default `ap_manager` does —
could still create and execute the same run end-to-end with no second human.
This control closes that gap by comparing the actor's identity to the run's
`initiated_by`.

Mirrors `approval_chain.check_segregation` (uploader != invoice approver): it is
**default-on** and disabled only by an explicit per-org opt-out for genuine
single-operator accounts —
``Organization.settings.payments.require_run_segregation: false``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException, status


def run_segregation_enabled(payment_config: dict | None) -> bool:
    """Whether maker-checker is enforced for this org's payment runs.

    Default ON. Only an explicit ``false`` on
    ``settings.payments.require_run_segregation`` turns it off (a missing key,
    ``None``, or any other value keeps the secure default).
    """
    return (payment_config or {}).get("require_run_segregation", True) is not False


def check_run_segregation(
    initiated_by: uuid.UUID | None,
    actor_id: uuid.UUID,
    payment_config: dict | None,
    *,
    action: str,
) -> None:
    """Raise 403 if ``actor_id`` is the user who created the run.

    ``action`` is a human verb for the message ("execute" / "approve"). Skips
    when the org opted out, or when ``initiated_by`` is NULL (a legacy run with
    no recorded creator — nothing to compare against).
    """
    if not run_segregation_enabled(payment_config):
        return
    if initiated_by is None:
        return
    if initiated_by == actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Segregation of duties: the user who created this payment run "
                f"cannot also {action} it. A different user must {action} it."
            ),
        )


# ── CFO-approval threshold ───────────────────────────────────────────────


#: Why the CFO gate fired. A fixed, PII-free vocabulary — these strings ride
#: audit rows and logs.
CFO_REASON_ABOVE_THRESHOLD = "above_threshold"
CFO_REASON_THRESHOLD_UNPARSEABLE = "threshold_unparseable"
CFO_REASON_AMOUNT_NOT_EXPRESSIBLE = "amount_not_expressible_in_reporting_currency"


@dataclass(frozen=True)
class CfoThresholdDecision:
    """Whether an outflow needs CFO sign-off, and why.

    ``evaluated_amount`` / ``currency`` record WHAT was compared, because for a
    foreign-currency payable that is not the figure the run reports as its
    total — a GBP 9,000 run is gated on its USD 11,400 equivalent. Both are
    PII-free and belong on the audit row so the decision is reconstructable.
    ``evaluated_amount`` is ``None`` when no comparison was possible.
    """

    required: bool
    reason: str | None = None
    threshold: Decimal | None = None
    evaluated_amount: Decimal | None = None
    currency: str | None = None


def cfo_approval_decision(
    *,
    payment_config: dict | None,
    reporting_amount: Decimal,
    reporting_currency: str,
    unconverted: bool,
) -> CfoThresholdDecision:
    """Does this outflow clear the org's ``payments.cfo_approval_above`` gate?

    **The threshold is a bare number denominated in the org's REPORTING
    currency**, exactly like ``settings.expense_approval.cfo_threshold`` — so
    the amount has to be expressed in that currency before it can be compared.
    It used to be compared against the raw invoice-currency figure, which made
    the gate fail OPEN for every foreign-currency payable priced below the
    threshold in its own units: a GBP 9,000 run (USD 11,400) slipped under a
    USD 10,000 threshold and executed with no CFO sign-off. Refusing a
    *mixed*-currency run — which ``create_payment_run_for_invoices`` already
    does — closes the batch half of that hole and none of this one.

    Fails **closed** on both kinds of missing information, matching
    ``services/expense_currency``:

    * an unparseable threshold (a typo'd settings value) requires sign-off
      rather than silently disabling the control; and
    * an amount that cannot be expressed in the reporting currency (no rate
      locked on the invoice row, or a lock that no longer describes its
      currency pair) is treated as *over* the threshold, never under it.

    A threshold that is absent, ``0`` or negative disables the gate entirely —
    unchanged, and the only path that returns ``required=False`` without a
    comparison. Pure: no DB, no clock, no FX call.
    """
    raw = (payment_config or {}).get("cfo_approval_above")
    currency = (reporting_currency or "USD").strip().upper()
    if raw is None:
        return CfoThresholdDecision(required=False, currency=currency)

    try:
        threshold = Decimal(str(raw))
    except (ValueError, ArithmeticError):
        return CfoThresholdDecision(
            required=True,
            reason=CFO_REASON_THRESHOLD_UNPARSEABLE,
            currency=currency,
        )

    if threshold <= 0:
        return CfoThresholdDecision(required=False, threshold=threshold, currency=currency)

    if unconverted:
        return CfoThresholdDecision(
            required=True,
            reason=CFO_REASON_AMOUNT_NOT_EXPRESSIBLE,
            threshold=threshold,
            currency=currency,
        )

    # Strict `>` matches the setting name (`cfo_approval_above`): a total
    # exactly AT the threshold does not require sign-off.
    over = reporting_amount > threshold
    return CfoThresholdDecision(
        required=over,
        reason=(CFO_REASON_ABOVE_THRESHOLD if over else None),
        threshold=threshold,
        evaluated_amount=reporting_amount,
        currency=currency,
    )
