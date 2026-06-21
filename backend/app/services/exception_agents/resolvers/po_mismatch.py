"""Dispatcher for the ``po_mismatch`` exception type.

A single ``po_mismatch`` exception covers several distinct invoice↔PO problems,
each with its own auto-resolution strategy:

  * a clean **amount variance** against a fully-matched PO → ``amount_mismatch_v1``
    (snap the amount to the PO total + approve);
  * a **missing / unresolved PO** (the referenced number resolves to nothing) →
    ``missing_po_v1`` (find the real PO by vendor + amount + date, link it, approve);
  * a **consolidated invoice spanning several POs** (no single PO matches, but a
    unique PO *set* sums to the total within tolerance) → ``multi_po_split_v1``
    (link the whole set, approve; never adjusts the amount).

The exception-agent registry is keyed by ``exception_type`` (one resolver per
type), so this dispatcher is the single registered ``po_mismatch`` resolver. It
owns an ordered list of delegate resolvers and, in ``evaluate``, tries each in
turn until one recommends ``auto_resolved``; that delegate's recommendation —
**and its ``agent_type``** — becomes the dispatcher's result, so the coordinator
records the right resolver in the ``AgentDecision``. If none recommends a fix,
the dispatcher escalates (carrying the most specific delegate's rationale).

The delegates are disjoint:

  * ``matched`` live status → amount-mismatch;
  * ``no_po`` + exactly ONE PO matching the full amount → missing-PO;
  * ``no_po`` + NO single PO matching but a unique PO set summing to the total →
    multi-PO split. ``multi_po_split_v1`` explicitly defers when a single PO
    matches (so the single-PO resolver, tried first, always wins that case),

so at most one ever fires; ordering only decides the rationale on a full
escalation. ``apply`` is delegated to whichever resolver ``evaluate`` selected —
the dispatcher never mutates state itself.
"""

from __future__ import annotations

from app.services.exception_agents.base import (
    ACTION_AUTO_RESOLVED,
    AgentEvaluation,
    ExceptionResolver,
)
from app.services.exception_agents.registry import register_exception_agent
from app.services.exception_agents.resolvers.amount_mismatch import AmountMismatchResolver
from app.services.exception_agents.resolvers.missing_po import MissingPOResolver
from app.services.exception_agents.resolvers.multi_po_split import MultiPOSplitResolver


@register_exception_agent("po_mismatch")
class PoMismatchDispatcher(ExceptionResolver):
    exception_type = "po_mismatch"
    # Default agent_type until evaluate selects a delegate (used on escalation
    # when no delegate recommends a fix). Overwritten with the chosen delegate's
    # agent_type when one recommends auto_resolve.
    agent_type = "po_mismatch_dispatch_v1"

    def __init__(self) -> None:
        # Fresh delegate instances per dispatch (the registry returns a fresh
        # dispatcher per call, so delegate per-evaluate state is isolated too).
        self._delegates: list[ExceptionResolver] = [
            AmountMismatchResolver(),
            MissingPOResolver(),
            MultiPOSplitResolver(),
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
                # This delegate owns the resolution — surface its agent_type so
                # the coordinator's AgentDecision records the real resolver.
                self._selected = delegate
                self.agent_type = delegate.agent_type
                return evaluation
        # Nobody can auto-fix — escalate. Carry the last delegate's rationale
        # (the most specific one tried). agent_type stays the dispatcher's so the
        # decision log shows the type was triaged but no resolver acted.
        self._selected = None
        return last_eval  # always set: there is at least one delegate

    async def apply(self, db, *, exception, invoice, evaluation, actor_id) -> None:
        """Delegate the mutation to whichever resolver evaluate selected. Only
        reached when evaluate recommended auto_resolve (coordinator gate), so
        ``_selected`` is set; guard defensively anyway."""
        if self._selected is None:
            return None
        return await self._selected.apply(
            db,
            exception=exception,
            invoice=invoice,
            evaluation=evaluation,
            actor_id=actor_id,
        )
