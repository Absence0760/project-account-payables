"""Segregation-of-duties controls on the payment-run money path.

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
