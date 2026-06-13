import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntityMixin, TimestampMixin


class AgentDecision(Base, EntityMixin, TimestampMixin):
    """Append-only log of every autonomous exception-agent run.

    One row per coordinator invocation against one exception. Intent is
    append-only — rows are never updated or deleted (the SOX audit-immutability
    triggers are on `audit_log`, not here; append-only is enforced by
    convention + the absence of any UPDATE/DELETE path in code). The parallel
    `audit_log` row for any invoice mutation IS DB-immutable.
    """

    __tablename__ = "agent_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exception_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exceptions.id"), nullable=False, index=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False, index=True
    )
    exception_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # auto_resolved | escalated | no_action
    action_taken: Mapped[str] = mapped_column(String(20), nullable=False)
    # 0.0000–1.0000; Numeric (never float — confidence is compared to a
    # threshold, but kept exact for reproducibility of the decision log).
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0"))
    rationale: Mapped[str | None] = mapped_column(Text)
    # {"field": {"old": "<str>", "new": "<str>"}} — money serialised as
    # string-Decimal (mirrors audit_access.build_field_diff). NULL when no change.
    changes: Mapped[dict | None] = mapped_column(JSONB)
    # The org autonomy level in force at decision time (conservative|balanced|aggressive).
    autonomy_level: Mapped[str] = mapped_column(String(20), nullable=False, default="conservative")
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # created_at comes from TimestampMixin.
