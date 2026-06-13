import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntityMixin, TimestampMixin


class WorkflowSuggestion(Base, EntityMixin, TimestampMixin):
    """Advisory adaptive-workflow suggestion (tenant-scoped).

    Materialized cache of `services.adaptive_workflows.derive_suggestions`,
    keyed by `dedupe_key` so a dismissal survives recomputation. ADVISORY:
    nothing here is auto-applied — `status='applied'` is set only by a future
    explicit admin action that routes through the audited approval/workflow
    path. See backend/docs/adaptive-workflows.md.
    """

    __tablename__ = "workflow_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # 0.00–99.99 confidence; Numeric, never float.
    confidence_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    # open | dismissed | applied | stale
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # created_at / updated_at from TimestampMixin.

    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_workflow_suggestions_dedupe_key"),)
