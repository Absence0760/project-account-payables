"""Notification model — in-app notification center rows (tenant-scoped).

One row per (recipient, event). Lives in the tenant DB alongside invoices so
the notification center query is tenant-isolated through `get_tenant_db()`.
`recipient_user_id` references a control-plane `users.id` but carries no FK —
the User lives in a different database, so the link is by value only.

Notification *preferences* are user-global and live on the control-plane
`User.notification_prefs` JSONB column, not here.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin

# Event types — the closed set of notifiable invoice lifecycle events.
EVENT_INVOICE_ASSIGNED = "invoice_assigned"
EVENT_INVOICE_APPROVED = "invoice_approved"
EVENT_INVOICE_REJECTED = "invoice_rejected"
EVENT_INVOICE_PAID = "invoice_paid"

NOTIFICATION_EVENT_TYPES = (
    EVENT_INVOICE_ASSIGNED,
    EVENT_INVOICE_APPROVED,
    EVENT_INVOICE_REJECTED,
    EVENT_INVOICE_PAID,
)


class Notification(Base, TenantMixin, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Carry the originating invoice's correlation_id so a shipped audit row and
    # the notification it spawned share one thread of causation.
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    # Control-plane users.id — no FK (cross-DB). Indexed for the per-user list.
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False, default="invoice")
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    # NULL = unread. Set to now() when the recipient marks it read.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_notifications_recipient", "recipient_user_id"),
        # Powers the unread-count query: WHERE recipient_user_id = ? AND read_at IS NULL.
        Index("ix_notifications_recipient_unread", "recipient_user_id", "read_at"),
    )
