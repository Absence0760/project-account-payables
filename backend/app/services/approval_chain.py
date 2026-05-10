"""Approval chain engine — multi-level routing, delegation, segregation.

Handles:
- Multi-level approval chains (amount-based level resolution, sequential advancement)
- Delegation / out-of-office proxy resolution
- Segregation of duties (uploader ≠ approver)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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


def _evaluate_routing_rule(rule: dict, attrs: dict) -> bool:
    """Evaluate a single RoutingRule against an attrs dict (extracted from
    the invoice). Unknown fields and unknown operators short-circuit to
    True so a stale UI config cannot harden into a 403 / approval block."""
    field = rule.get("field")
    op = rule.get("operator")
    value = rule.get("value")
    actual = attrs.get(field) if field else None

    if op == "eq":
        return actual == value
    if op == "ne":
        return actual != value
    if op == "in":
        return actual in (value or []) if isinstance(value, list) else False
    if op == "not_in":
        return actual not in (value or []) if isinstance(value, list) else True
    if op == "starts_with":
        return bool(actual) and isinstance(value, str) and str(actual).startswith(value)
    # Unknown operator — fail open so the level still applies.
    return True


def _level_routing_matches(level: dict, attrs: dict) -> bool:
    """All routing_rules on a level AND-compose. An empty rules list always
    matches."""
    rules = level.get("routing_rules") or []
    return all(_evaluate_routing_rule(rule, attrs) for rule in rules)


def resolve_applicable_levels(
    chain: list[dict],
    amount: float,
    *,
    invoice_attrs: dict | None = None,
) -> list[dict]:
    """Filter chain levels to those that apply to this invoice.

    A level applies when:
    - min_amount is None OR amount >= min_amount
    - max_amount is None OR amount <= max_amount
    - every routing_rule evaluates True against `invoice_attrs`

    `invoice_attrs` keys map onto RoutingField (gl_account, cost_center,
    department, vendor_id). Callers should populate it from the Invoice
    row; missing keys behave like None and only match `ne` / `not_in`
    rules.
    """
    attrs = invoice_attrs or {}
    applicable = []
    for level in chain:
        min_amt = level.get("min_amount")
        max_amt = level.get("max_amount")
        if min_amt is not None and amount < min_amt:
            continue
        if max_amt is not None and amount > max_amt:
            continue
        if not _level_routing_matches(level, attrs):
            continue
        applicable.append(level)
    return applicable


def invoice_routing_attrs(invoice) -> dict:
    """Extract routing-relevant attributes off an Invoice row. `department`
    falls back to the GL-account prefix if no explicit column exists yet."""
    return {
        "gl_account": getattr(invoice, "gl_account", None),
        "cost_center": getattr(invoice, "cost_center", None),
        "department": getattr(invoice, "department", None),
        "vendor_id": str(invoice.vendor_id) if getattr(invoice, "vendor_id", None) else None,
    }


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
    now_iso = datetime.now(UTC).isoformat()
    levels_state = []
    for i, level in enumerate(applicable_levels):
        levels_state.append(
            {
                "level": i,
                "name": level.get("name", f"Level {i + 1}"),
                "required": level.get("required_approvals", 1),
                "approver_ids": list(level.get("approver_ids", [])),
                "approvals": [],
                "parallel_mode": level.get("parallel_mode", "any"),
                "escalation_hours": level.get("escalation_hours"),
                "escalation_to_user_ids": list(level.get("escalation_to_user_ids", [])),
                # Stamped on level entry; the escalation sweeper compares
                # this to wall-clock now to decide if the level is stale.
                # Only the level whose `level == current_level` carries an
                # `entered_at` worth acting on.
                "entered_at": now_iso if i == 0 else None,
                "escalations": [],
            }
        )

    state = dict(instance.state_data or {})
    state["approval_levels"] = {
        "levels": levels_state,
        "current_level": 0,
    }
    instance.state_data = state


def _level_satisfied(level: dict) -> bool:
    """`any` mode: distinct approver count >= required. `all` mode: every
    listed approver has approved at least once."""
    distinct = {a["user_id"] for a in level.get("approvals", [])}
    if level.get("parallel_mode") == "all":
        return all(uid in distinct for uid in level.get("approver_ids", []))
    return len(distinct) >= level.get("required", 1)


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
    now_iso = datetime.now(UTC).isoformat()

    # Record this approval
    current_level["approvals"].append(
        {
            "user_id": str(actor_id),
            "at": now_iso,
        }
    )

    # Check if current level is satisfied
    if _level_satisfied(current_level):
        chain_state["current_level"] = current_idx + 1

        if chain_state["current_level"] >= len(levels):
            instance.state_data = state
            return True

        # Stamp entry time on the newly-active level so the sweeper has a
        # clock to read.
        next_level = levels[chain_state["current_level"]]
        next_level["entered_at"] = now_iso

    instance.state_data = state
    return False


# ------------------------------------------------------------------
# Escalation
# ------------------------------------------------------------------


def apply_escalation(instance: WorkflowInstance, *, now: datetime | None = None) -> bool:
    """If the current level has been stale longer than its escalation_hours,
    append `escalation_to_user_ids` to the level's approver_ids list (deduped)
    and record an escalation event. Returns True if the instance was mutated.

    Idempotent — once a level is escalated to a given user set, re-running
    is a no-op."""
    now = now or datetime.now(UTC)
    state = dict(instance.state_data or {})
    chain_state = state.get("approval_levels")
    if not chain_state:
        return False
    levels = chain_state.get("levels", [])
    current_idx = chain_state.get("current_level", 0)
    if current_idx >= len(levels):
        return False
    level = levels[current_idx]
    hours = level.get("escalation_hours")
    targets = level.get("escalation_to_user_ids") or []
    entered_at = level.get("entered_at")
    if not hours or not targets or not entered_at:
        return False
    try:
        entered_dt = datetime.fromisoformat(entered_at)
    except ValueError:
        return False
    age = now - entered_dt
    if age < timedelta(hours=hours):
        return False

    existing = set(level.get("approver_ids", []))
    new_targets = [uid for uid in targets if uid not in existing]
    if not new_targets:
        return False  # already escalated to these users — idempotent

    level["approver_ids"] = list(existing | set(new_targets))
    level.setdefault("escalations", []).append(
        {
            "at": now.isoformat(),
            "added_user_ids": new_targets,
            "after_hours": hours,
        }
    )
    instance.state_data = state
    return True
