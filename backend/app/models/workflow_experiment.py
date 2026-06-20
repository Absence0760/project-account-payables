import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntityMixin, TimestampMixin


class WorkflowExperiment(Base, EntityMixin, TimestampMixin):
    """A/B test of two workflow-rule configurations (tenant-scoped).

    Compares the performance of an **A** control config vs a **B** variant config
    on the same workflow definition. When an experiment is ``running`` and an
    invoice is created for the matching ``(org, entity, workflow_definition)``,
    the invoice is deterministically assigned to A or B (stable hash of invoice
    id + experiment id, honouring ``split_a_pct``); the chosen variant's config
    is snapshotted onto that invoice's workflow instance (respecting the
    "frozen per-invoice snapshot" invariant), and the assignment is recorded on
    this experiment's ``assignments`` map so it survives recomputation and is
    auditable.

    NEVER moves money — it only routes (which config an invoice runs under) and
    measures. See backend/docs/adaptive-workflows.md § A/B testing.
    """

    __tablename__ = "workflow_experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # The workflow definition under test. When an experiment is running, only
    # invoices whose resolved workflow definition is this one are assigned.
    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False, index=True
    )

    # The two variant configs — each a full steps_config JSONB (same shape the
    # workflow definition stores), snapshotted onto the assigned invoice's
    # instance. Config_a is the control; config_b is the variant.
    config_a: Mapped[dict] = mapped_column(JSONB, nullable=False)
    config_b: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Split ratio — percent of invoices routed to A (0..100). 50 = even split.
    split_a_pct: Mapped[int] = mapped_column(nullable=False, default=50)

    # The metric the winner call is made on (one of services.workflow_experiments
    # .PRIMARY_METRICS) + the minimum completed-invoice sample per arm before a
    # winner is called.
    primary_metric: Mapped[str] = mapped_column(
        String(40), nullable=False, default="time_to_approval_days"
    )
    min_sample_per_variant: Mapped[int] = mapped_column(nullable=False, default=10)

    # draft | running | concluded
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # invoice_id (str) → "A" | "B". The recorded, stable assignment per in-flight
    # invoice, so the split is auditable and reproducible without re-hashing.
    assignments: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # created_at / updated_at from TimestampMixin.

    __table_args__ = (
        Index(
            "ix_workflow_experiments_org_status",
            "organization_id",
            "status",
        ),
    )
