"""A/B testing — the DB-touching runtime that the workflow engine calls.

Kept separate from the pure ``services/workflow_experiments`` (which stays IO-free
and unit-testable) so the workflow engine has one async entry point:
``maybe_assign_experiment_variant``. Called from
``workflow_engine.create_workflow_instance`` — the single chokepoint where the
per-invoice ``steps_config_snapshot`` is frozen — and is best-effort there (its
caller swallows exceptions so experiment routing can never break invoice
creation).

Selection: a ``running`` ``WorkflowExperiment`` whose ``workflow_definition_id``
equals the invoice's resolved definition and whose ``entity_id`` is compatible
(experiment scoped to the invoice's entity, or org-wide / NULL). The deterministic
``assign_variant`` (stable hash of invoice id + experiment id, honouring
``split_a_pct``) picks A or B; the chosen variant's config is returned for the
snapshot and the assignment is recorded on the experiment's ``assignments`` map +
an ``invoice.experiment_assigned`` audit row (PII-free).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.workflow import WorkflowDefinition
from app.models.workflow_experiment import WorkflowExperiment
from app.services.audit_dispatch import dispatch_audit
from app.services.workflow_experiments import VARIANT_A, assign_variant

_log = logging.getLogger(__name__)


async def maybe_assign_experiment_variant(
    db: AsyncSession,
    invoice: Invoice,
    defn: WorkflowDefinition,
) -> dict | None:
    """Assign the invoice to a running experiment's variant, if one applies.

    Returns the chosen variant's ``steps_config`` dict to snapshot onto the
    invoice's workflow instance, or ``None`` when no experiment matches (the
    caller then uses the live definition's config). Records the assignment on the
    experiment + writes a PII-free ``invoice.experiment_assigned`` audit row.

    At most one experiment is honoured per invoice (the most recently started
    matching one); running two experiments over the same definition is a config
    mistake, not a supported split, so we deterministically pick one rather than
    compound the snapshots.
    """
    q = (
        select(WorkflowExperiment)
        .where(
            WorkflowExperiment.organization_id == invoice.organization_id,
            WorkflowExperiment.workflow_definition_id == defn.id,
            WorkflowExperiment.status == "running",
        )
        .order_by(WorkflowExperiment.started_at.desc().nullslast())
    )
    experiments = (await db.execute(q)).scalars().all()
    # Filter to experiments whose entity scope is compatible with the invoice:
    # an org-wide (NULL entity) experiment matches any invoice; an entity-scoped
    # one matches only invoices in that entity.
    match: WorkflowExperiment | None = None
    for exp in experiments:
        if exp.entity_id is None or exp.entity_id == invoice.entity_id:
            match = exp
            break
    if match is None:
        return None

    variant = assign_variant(str(invoice.id), str(match.id), split_a_pct=match.split_a_pct)
    config = match.config_a if variant == VARIANT_A else match.config_b

    # Record the assignment durably (reassign the dict so SQLAlchemy flags the
    # JSONB column dirty — in-place mutation of a JSONB dict isn't tracked).
    assignments = dict(match.assignments or {})
    assignments[str(invoice.id)] = variant
    match.assignments = assignments

    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id or uuid.uuid4(),
        organization_id=invoice.organization_id,
        actor_id=None,
        action="invoice.experiment_assigned",
        entity_type="invoice",
        entity_id=invoice.id,
        details={
            "experiment_id": str(match.id),
            "experiment_name": match.name,
            "variant": variant,
            "workflow_definition_id": str(defn.id),
        },
    )
    return config
