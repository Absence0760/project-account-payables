"""Dispatcher for the ``missing_data`` exception type.

A ``missing_data`` exception covers several distinct "the invoice is not yet
codeable / payable" gaps, each with its own auto-resolution strategy. Today one
strategy is implemented:

  * a **missing / inconsistent GL account** (the only material gap) →
    ``gl_coding_v1`` (fill or correct the GL — and an empty cost center — from the
    vendor's dominant historical coding, then approve).

Genuinely missing vendor / amount / invoice-number gaps have no auto-fix and fall
through to an escalation (a GL fix wouldn't make such an invoice payable).

The exception-agent registry is keyed by ``exception_type`` (one resolver per
type), so this dispatcher is the single registered ``missing_data`` resolver — it
mirrors ``po_mismatch.PoMismatchDispatcher``. It owns an ordered list of delegate
resolvers and, in ``evaluate``, tries each in turn until one recommends
``auto_resolved``; that delegate's recommendation — **and its ``agent_type``** —
becomes the dispatcher's result, so the coordinator records the real resolver in
the ``AgentDecision``. If none recommends a fix, the dispatcher escalates
(carrying the most specific delegate's rationale). ``apply`` is delegated to the
selected resolver — the dispatcher never mutates state itself.
"""

from __future__ import annotations

from app.services.exception_agents.base import (
    ACTION_AUTO_RESOLVED,
    AgentEvaluation,
    ExceptionResolver,
)
from app.services.exception_agents.registry import register_exception_agent
from app.services.exception_agents.resolvers.gl_coding import GLCodingResolver


@register_exception_agent("missing_data")
class MissingDataDispatcher(ExceptionResolver):
    exception_type = "missing_data"
    # Default agent_type until evaluate selects a delegate (used on escalation
    # when no delegate recommends a fix).
    agent_type = "missing_data_dispatch_v1"

    def __init__(self) -> None:
        self._delegates: list[ExceptionResolver] = [
            GLCodingResolver(),
        ]
        self._selected: ExceptionResolver | None = None

    async def evaluate(self, db, *, exception, invoice, org_settings) -> AgentEvaluation:
        last_eval: AgentEvaluation | None = None
        for delegate in self._delegates:
            evaluation = await delegate.evaluate(
                db, exception=exception, invoice=invoice, org_settings=org_settings
            )
            last_eval = evaluation
            if evaluation.recommended_action == ACTION_AUTO_RESOLVED:
                self._selected = delegate
                self.agent_type = delegate.agent_type
                return evaluation
        self._selected = None
        return last_eval  # always set: there is at least one delegate

    async def apply(self, db, *, exception, invoice, evaluation, actor_id) -> None:
        if self._selected is None:
            return None
        return await self._selected.apply(
            db,
            exception=exception,
            invoice=invoice,
            evaluation=evaluation,
            actor_id=actor_id,
        )
