"""Deferred resolvers — registered so the coordinator can dispatch by type, but
they always escalate (the real logic is out of scope for this slice; see
``docs/exception-agents.md`` "Deferred"). Each gets a follow-up roadmap line."""

from decimal import Decimal

from app.services.exception_agents.base import (
    ACTION_ESCALATED,
    AgentEvaluation,
    ExceptionResolver,
)
from app.services.exception_agents.registry import register_exception_agent

_DECIMAL_ZERO = Decimal("0")


def _escalate_stub(etype: str, agent: str, reason: str):
    @register_exception_agent(etype)
    class _Stub(ExceptionResolver):
        agent_type = agent
        exception_type = etype

        async def evaluate(self, db, *, exception, invoice, org_settings):
            return AgentEvaluation(
                recommended_action=ACTION_ESCALATED,
                confidence=_DECIMAL_ZERO,
                rationale=reason,
            )

    return _Stub


_escalate_stub(
    "missing_data",
    "missing_data_stub_v0",
    "Missing-data auto-resolution is not yet available; escalating to a human.",
)
_escalate_stub(
    "duplicate",
    "duplicate_stub_v0",
    "Duplicate auto-merge is not yet available; escalating to a human.",
)
_escalate_stub(
    "fraud_flag",
    "fraud_stub_v0",
    "Fraud-flag review requires a human; escalating.",
)
