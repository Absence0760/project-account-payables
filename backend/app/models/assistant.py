"""Conversational AP Assistant — persistence models.

Two planes (see ``docs/conversational-assistant.md`` for the decision):

  - ``Conversation`` / ``ConversationMessage`` are **tenant-scoped** (live in
    each ``feoh_<slug>`` DB). Conversation content is tenant business data and
    must inherit tenant isolation: every read filters
    ``(organization_id == jwt_org, user_id == current_user.id)``.

  - ``AssistantUsage`` is **control-plane** (lives in ``feohledger``,
    next to ``ExtractionUsage``). The token budget is a per-org cap; a single
    upsert-on-``(org, period)`` row enforces it without fanning a sum across
    every tenant DB on each chat call.
"""

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    """A chat thread, scoped to ``(organization_id, user_id)``. Tenant-scoped."""

    __tablename__ = "assistant_conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Cross-checked against the JWT org claim on every read/write.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Owner — a control-plane User.id (no cross-DB FK; this lives in a tenant DB).
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )


class ConversationMessage(Base, TimestampMixin):
    """One user or assistant turn within a conversation. Tenant-scoped."""

    __tablename__ = "assistant_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # For assistant turns: [{tool, args, result, error}] structured output, used
    # for chart rendering later. PII-safe arg summary only (same shape as audit).
    tool_calls: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AssistantUsage(Base, TimestampMixin):
    """Per-org / per-month token meter for the assistant. Control-plane.

    Mirrors the ``ExtractionUsage`` billing pattern — one upsert row per
    ``(organization_id, period)``. The single source of truth for the monthly
    budget cap and for ``GET /api/assistant/usage``.
    """

    __tablename__ = "assistant_usage"
    __table_args__ = (
        UniqueConstraint("organization_id", "period", name="uq_assistant_usage_org_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "YYYY-MM"
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
