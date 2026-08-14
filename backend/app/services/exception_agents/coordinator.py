from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_decision import AgentDecision
from app.models.exception import Exception as APException
from app.models.invoice import Invoice
from app.services.exception_agents.autonomy import (
    autonomy_threshold,
    resolve_autonomy_level,
)
from app.services.exception_agents.base import (
    ACTION_AUTO_RESOLVED,
    ACTION_ESCALATED,
    ACTION_NO_ACTION,
)
from app.services.exception_agents.registry import get_resolver
from app.services.exception_agents.resolvers.amount_mismatch import NotApprovable
from app.services.exception_lifecycle import record_decision

logger = logging.getLogger(__name__)

#: What the queue shows as `resolved_by` for an agent decision. The audit row's
#: `actor_id` still names the human who triggered the run.
AGENT_ACTOR_NAME = "AP Agent"


class ExceptionNotActionable(Exception):  # noqa: N818
    """Raised when, after taking the row lock, the exception is no longer in an
    actionable state ('open'|'escalated') — a concurrent run already resolved
    it. The API maps this to 409 (the same status the pre-lock check returns)."""

    def __init__(self, status: str):
        self.status = status
        super().__init__(f"Exception is '{status}', not actionable.")


@dataclass
class AgentRunResult:
    decision: AgentDecision
    exception: APException


async def run_agent(
    db: AsyncSession,
    *,
    exception: APException,
    actor_id: uuid.UUID,
    org_settings: dict | None,
    actor_roles: set[str] | None = None,
) -> AgentRunResult:
    """Run the matching resolver on one OPEN/ESCALATED exception.

    Flow:
      0. Lock the exception row FOR UPDATE and re-assert it is still actionable
         (serializes concurrent agent-resolve calls — the API status check is a
         TOCTOU otherwise; two callers could both pass it, both evaluate, and
         both write a decision row + clobber the exception status).
      1. Resolve org autonomy_level → confidence threshold.
      2. Find the resolver for this exception_type (none → no_action).
      3. resolver.evaluate(...) → AgentEvaluation (no mutation).
      4. If recommended==auto_resolved AND confidence >= threshold:
           resolver.apply(...) mutates + writes audit_log rows;
           resolve the exception through the SHARED queue chokepoint
           (services/exception_lifecycle.record_decision — the same one the
           human queue uses, so bookkeeping and the append-only
           `exception.resolved` audit row can't drift between the two).
         else:
           escalate through that same chokepoint (status=escalated).
      5. Persist ONE AgentDecision row (always) and commit.
    """
    # Serialize concurrent runs on the same exception: take a row lock and
    # re-read the current status. A loser that finds the exception already
    # left ('open'|'escalated') aborts before any mutation or decision write.
    # populate_existing() forces the row attributes to refresh from the locked
    # read — without it the identity map would hand back the caller's already
    # loaded (pre-lock, possibly stale) instance and the status re-check below
    # would see the OLD value, defeating the lock.
    locked_exc = (
        await db.execute(
            select(APException)
            .where(APException.id == exception.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if locked_exc.status not in ("open", "escalated"):
        raise ExceptionNotActionable(locked_exc.status)
    # Operate on the locked, freshly-read row for the rest of the run.
    exception = locked_exc

    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == exception.invoice_id))
    ).scalar_one()

    level = resolve_autonomy_level(org_settings)
    threshold = autonomy_threshold(level)

    resolver = get_resolver(exception.exception_type)
    if resolver is None:
        # No resolver registered — log the decision but leave the exception's
        # queue status untouched (there is nothing to auto-do, and nothing to
        # escalate beyond its current state).
        decision = _record(
            db,
            exception,
            invoice,
            action=ACTION_NO_ACTION,
            confidence=0,
            rationale=(f"No agent registered for exception type '{exception.exception_type}'."),
            changes=None,
            level=level,
            agent_type="none",
        )
        await db.commit()
        return AgentRunResult(decision=decision, exception=exception)

    evaluation = await resolver.evaluate(
        db, exception=exception, invoice=invoice, org_settings=org_settings or {}
    )

    can_resolve = (
        evaluation.recommended_action == ACTION_AUTO_RESOLVED and evaluation.confidence >= threshold
    )

    # Fail-closed authority check. An auto-resolve APPROVES the invoice through
    # review.approve_invoice with the triggering user's roles (the CFO gate +
    # segregation-of-duties key off actor_roles). A run that can't name the
    # acting user's real roles must NOT self-approve on a fabricated elevated
    # set — it escalates to a human. This is the single chokepoint that
    # guarantees actor_roles is populated before any resolver.apply runs, so the
    # leaf resolvers thread it straight through without a hardcoded fallback.
    if can_resolve and not actor_roles:
        await _escalate(
            db,
            exception,
            invoice,
            actor_id,
            "Auto-resolution withheld: the triggering actor's roles are unknown.",
        )
        decision = _record(
            db,
            exception,
            invoice,
            action=ACTION_ESCALATED,
            confidence=evaluation.confidence,
            rationale=(
                "Auto-resolution withheld: the triggering actor's roles are "
                "unknown, so the agent cannot approve on their behalf without "
                "fabricating authority. Escalated to a human."
            ),
            changes=None,
            level=level,
            agent_type=resolver.agent_type,
        )
        await db.commit()
        return AgentRunResult(decision=decision, exception=exception)

    if can_resolve:
        try:
            # resolver.apply MUST write the audit_log row(s) for the mutation.
            await resolver.apply(
                db,
                exception=exception,
                invoice=invoice,
                evaluation=evaluation,
                actor_id=actor_id,
                actor_roles=actor_roles,
            )
        except NotApprovable as exc:
            # The invoice can't legally reach `approved` from its current state.
            # Nothing committed yet — downgrade to an escalation.
            logger.info(
                "Agent could not auto-approve invoice %s (status=%s); escalating",
                invoice.id,
                exc.status,
            )
            await _escalate(
                db,
                exception,
                invoice,
                actor_id,
                f"Could not auto-approve: invoice is '{exc.status}', not ready_for_review.",
            )
            decision = _record(
                db,
                exception,
                invoice,
                action=ACTION_ESCALATED,
                confidence=evaluation.confidence,
                rationale=(
                    f"Could not auto-approve: invoice is '{exc.status}', not "
                    "ready_for_review. Escalated to a human."
                ),
                changes=None,
                level=level,
                agent_type=resolver.agent_type,
            )
            await db.commit()
            return AgentRunResult(decision=decision, exception=exception)

        await record_decision(
            db,
            exception=exception,
            action="resolve",
            resolution=evaluation.rationale or "Auto-resolved by the AP agent.",
            actor_id=actor_id,
            actor_name=AGENT_ACTOR_NAME,
            invoice=invoice,
            via="agent",
        )
        action = ACTION_AUTO_RESOLVED
    else:
        await _escalate(
            db,
            exception,
            invoice,
            actor_id,
            evaluation.rationale
            or "Confidence below the org's autonomy threshold; escalated to a human.",
        )
        action = ACTION_ESCALATED

    decision = _record(
        db,
        exception,
        invoice,
        action=action,
        confidence=evaluation.confidence,
        rationale=evaluation.rationale,
        changes=evaluation.changes or None,
        level=level,
        agent_type=resolver.agent_type,
    )
    await db.commit()
    return AgentRunResult(decision=decision, exception=exception)


async def _escalate(
    db: AsyncSession,
    exception: APException,
    invoice: Invoice,
    actor_id: uuid.UUID,
    rationale: str,
) -> None:
    """Escalate through the shared queue chokepoint.

    Same bookkeeping + append-only audit row a human escalation writes — which
    also means the rationale now lands on the exception row itself, so the human
    picking the escalation up reads WHY in the queue instead of only in the
    AgentDecision log."""
    await record_decision(
        db,
        exception=exception,
        action="escalate",
        resolution=rationale,
        actor_id=actor_id,
        actor_name=AGENT_ACTOR_NAME,
        invoice=invoice,
        via="agent",
    )


def _record(
    db: AsyncSession,
    exception: APException,
    invoice: Invoice,
    *,
    action: str,
    confidence,
    rationale: str | None,
    changes: dict | None,
    level: str,
    agent_type: str,
) -> AgentDecision:
    row = AgentDecision(
        exception_id=exception.id,
        invoice_id=invoice.id,
        exception_type=exception.exception_type,
        action_taken=action,
        confidence=Decimal(str(confidence)),
        rationale=rationale,
        changes=changes,
        autonomy_level=level,
        agent_type=agent_type,
        organization_id=exception.organization_id,
        entity_id=getattr(invoice, "entity_id", None),
    )
    db.add(row)
    return row
