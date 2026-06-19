import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, EntityMixin, TimestampMixin


class WorkflowDefinition(Base, EntityMixin, TimestampMixin):
    __tablename__ = "workflow_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    steps_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_default: Mapped[bool] = mapped_column(default=False)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    instances: Mapped[list["WorkflowInstance"]] = relationship(back_populates="definition")

    # At most one is_default=true definition per (organization_id, entity_id),
    # treating a NULL entity (shared / org-wide) as a single sentinel so the
    # SQL NULL != NULL semantics don't let two shared defaults coexist. Mirrors
    # the uq_entities_one_default partial index in the entities table; lives here
    # so fresh tenants built via create_all (not Alembic) get it too (migration
    # 0050 installs it on existing tenants). See docs/multi-entity.md Phase 3.
    __table_args__ = (
        Index(
            "uq_workflow_definitions_one_default",
            text("organization_id"),
            text("COALESCE(entity_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=text("is_default = true"),
        ),
    )


class WorkflowVersion(Base):
    """Immutable snapshot of a WorkflowDefinition's ``steps_config``.

    Tenant-scoped history for the no-code builder. A new row is written
    on every manual "save version" and automatically before a PATCH that
    changes the live ``steps_config`` (so edit history is captured without
    a manual call), and on restore (the current state is snapshotted before
    the chosen version's steps are applied). Append-only by convention —
    the builder never mutates an existing row.
    """

    __tablename__ = "workflow_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    steps_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkflowInstance(Base, TimestampMixin):
    __tablename__ = "workflow_instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    current_step: Mapped[int] = mapped_column(default=0)
    state: Mapped[str] = mapped_column(String(30), default="active")
    state_data: Mapped[dict | None] = mapped_column(JSONB)
    steps_config_snapshot: Mapped[dict | None] = mapped_column(JSONB)

    definition: Mapped[WorkflowDefinition] = relationship(back_populates="instances")
    steps: Mapped[list["WorkflowStep"]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )


class WorkflowStep(Base, TimestampMixin):
    __tablename__ = "workflow_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_instances.id"), nullable=False
    )
    step_number: Mapped[int] = mapped_column(nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    original_assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approval_level: Mapped[int | None] = mapped_column()
    action: Mapped[str | None] = mapped_column(String(50))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    instance: Mapped[WorkflowInstance] = relationship(back_populates="steps")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Populated by the centralized audit-log shipper once the row has been
    # written to the WORM-compliant sink(s) (CloudWatch Logs + S3 Object
    # Lock). NULL means "not yet shipped" — the background loop picks those
    # up on the next tick. See services/audit_log_shipper.py.
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
