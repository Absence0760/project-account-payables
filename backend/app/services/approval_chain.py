"""Approval chain engine — multi-level routing, delegation, segregation.

Handles:
- Multi-level approval chains (amount-based level resolution, sequential advancement)
- Delegation / out-of-office proxy resolution
- Segregation of duties (uploader ≠ approver)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.user import User
from app.models.workflow import WorkflowInstance

# ------------------------------------------------------------------
# Segregation of duties
# ------------------------------------------------------------------


def check_segregation(
    invoice: Invoice,
    actor_id: uuid.UUID,
    approval_config: dict,
) -> None:
    """Raise 403 if the approver is the same user who uploaded the invoice.

    SoD is the classic AP invariant and a SOC 2 baseline — default-on. Orgs
    that need to disable it (e.g. single-operator accounts) must set
    ``require_segregation: false`` explicitly on the approval step config.

    Skips when:
    - require_segregation is explicitly set to False in the approval config
    - uploaded_by_id is NULL (pre-existing invoices)
    """
    if approval_config.get("require_segregation", True) is False:
        return
    if invoice.uploaded_by_id is None:
        return
    if invoice.uploaded_by_id == actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Segregation of duties: the user who uploaded this invoice cannot also approve it."
            ),
        )


# ------------------------------------------------------------------
# Delegation / out-of-office
# ------------------------------------------------------------------


async def resolve_assignee(
    user_id: uuid.UUID,
    control_db: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Check if the target user is OOO and resolve to their delegate.

    Returns (effective_assignee_id, original_id_or_none).
    If no delegation is active, returns (user_id, None).
    """
    result = await control_db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return user_id, None

    if user.delegate_to_id and user.delegate_until and user.delegate_until > datetime.now(UTC):
        return user.delegate_to_id, user_id

    return user_id, None


# ------------------------------------------------------------------
# Multi-level chain helpers
# ------------------------------------------------------------------


def resolve_applicable_levels(
    chain: list[dict],
    amount: float,
) -> list[dict]:
    """Filter chain levels to those whose amount range includes the invoice amount.

    Returns levels in order. A level applies when:
    - min_amount is None OR amount >= min_amount
    - max_amount is None OR amount <= max_amount
    """
    applicable = []
    for level in chain:
        min_amt = level.get("min_amount")
        max_amt = level.get("max_amount")
        if min_amt is not None and amount < min_amt:
            continue
        if max_amt is not None and amount > max_amt:
            continue
        applicable.append(level)
    return applicable


def get_chain_progress(instance: WorkflowInstance) -> dict:
    """Read the approval chain state from instance.state_data.

    Returns the chain state dict, or empty dict if not a chain workflow.
    """
    state = instance.state_data or {}
    return state.get("approval_levels", {})


def init_chain_state(
    instance: WorkflowInstance,
    applicable_levels: list[dict],
) -> None:
    """Initialize the approval chain state on a workflow instance.

    Called when an invoice enters the approval phase with strategy="chain".
    """
    levels_state = []
    for i, level in enumerate(applicable_levels):
        levels_state.append(
            {
                "level": i,
                "name": level.get("name", f"Level {i + 1}"),
                "required": level.get("required_approvals", 1),
                "approver_ids": level.get("approver_ids", []),
                "approvals": [],
            }
        )

    state = dict(instance.state_data or {})
    state["approval_levels"] = {
        "levels": levels_state,
        "current_level": 0,
    }
    instance.state_data = state


def advance_approval_chain(
    instance: WorkflowInstance,
    actor_id: uuid.UUID,
) -> bool:
    """Record an approval and advance the chain.

    Returns True if the chain is fully complete (all levels satisfied).
    Returns False if more approvals are needed.
    """
    state = dict(instance.state_data or {})
    chain_state = state.get("approval_levels", {})
    if not chain_state:
        return True  # no chain configured, treat as complete

    levels = chain_state.get("levels", [])
    current_idx = chain_state.get("current_level", 0)

    if current_idx >= len(levels):
        return True  # already past all levels

    current_level = levels[current_idx]

    # Record this approval
    current_level["approvals"].append(
        {
            "user_id": str(actor_id),
            "at": datetime.now(UTC).isoformat(),
        }
    )

    # Check if current level is satisfied
    if len(current_level["approvals"]) >= current_level["required"]:
        # Advance to next level
        chain_state["current_level"] = current_idx + 1

        # Check if all levels are done
        if chain_state["current_level"] >= len(levels):
            instance.state_data = state
            return True

    instance.state_data = state
    return False
