from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exception import Exception as APException
from app.models.invoice import Invoice

# action constants — single source of truth
ACTION_AUTO_RESOLVED = "auto_resolved"
ACTION_ESCALATED = "escalated"
ACTION_NO_ACTION = "no_action"


@dataclass
class AgentEvaluation:
    """A resolver's recommendation. Pure data — the coordinator decides whether
    to ACT on it based on the org autonomy threshold."""

    # The resolver's recommended action IF its confidence clears the threshold.
    # A resolver returns auto_resolved as its *recommendation*; the coordinator
    # downgrades to escalated when confidence < threshold.
    recommended_action: str
    confidence: Decimal  # 0..1
    rationale: str
    # {"field": {"old": "<str>", "new": "<str>"}} or {} — what WOULD change.
    changes: dict = field(default_factory=dict)


class ExceptionResolver(ABC):
    """One resolver per exception type. Stateless; instantiated per dispatch."""

    agent_type: str  # e.g. "amount_mismatch_v1"
    exception_type: str  # the single exception_type this resolver handles

    @abstractmethod
    async def evaluate(
        self,
        db: AsyncSession,
        *,
        exception: APException,
        invoice: Invoice,
        org_settings: dict,
    ) -> AgentEvaluation:
        """Decide (rules-first, LLM-rationale-optional). NO mutation here."""

    async def apply(
        self,
        db: AsyncSession,
        *,
        exception: APException,
        invoice: Invoice,
        evaluation: AgentEvaluation,
        actor_id: uuid.UUID,
        actor_roles: set[str] | None = None,
    ) -> None:
        """Mutate to enact the resolution. Override in resolvers that auto-fix.
        MUST write audit_log row(s) for any invoice mutation. Default no-op
        (used by escalate-only stubs).

        ``actor_roles`` is the triggering user's real role set — threaded into
        ``approve_invoice`` so the CFO gate + audit trail reflect who actually
        authorised the resolution, not a hardcoded role."""
        return None
