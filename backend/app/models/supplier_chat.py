"""Embedded supplier chat — per-invoice conversation between AP and the supplier.

Two tenant-scoped tables: one thread per invoice (lazy-created on first post)
and the append-only messages that hang off it. Follows ``contract.py`` patterns
(inline ``organization_id`` + ``EntityMixin`` on the parent; the child carries
neither — its scope is inherited through the parent thread, mirroring
``ContractLineItem`` / ``InvoiceLineItem``).

Author modeling is polymorphic by ``author_role`` + ``author_user_id`` with
**no FK** on ``author_user_id`` — it points across DBs/tables (AP messages →
control-plane ``users.id``; supplier messages → tenant ``vendor_users.id``;
``system`` posts carry NULL). See ``backend/docs/supplier-chat.md``.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, EntityMixin, TimestampMixin


class ChatThreadStatus(enum.StrEnum):
    open = "open"
    resolved = "resolved"


class ChatAuthorRole(enum.StrEnum):
    ap_team = "ap_team"  # an AP-side control-plane User (users.id)
    supplier = "supplier"  # a VendorUser (vendor_users.id, tenant DB)
    system = "system"  # template/automated posts (NULL author id)


class SupplierChatThread(Base, EntityMixin, TimestampMixin):
    __tablename__ = "supplier_chat_threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False, index=True
    )
    status: Mapped[ChatThreadStatus] = mapped_column(
        Enum(ChatThreadStatus, native_enum=False, length=20),
        default=ChatThreadStatus.open,
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Control-plane users.id — no FK (cross-DB).
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    messages: Mapped[list["SupplierChatMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="SupplierChatMessage.created_at",
    )

    __table_args__ = (Index("uq_supplier_chat_thread_invoice", "invoice_id", unique=True),)


class SupplierChatMessage(Base, TimestampMixin):
    __tablename__ = "supplier_chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("supplier_chat_threads.id"),
        nullable=False,
        index=True,
    )
    author_role: Mapped[ChatAuthorRole] = mapped_column(
        Enum(ChatAuthorRole, native_enum=False, length=20), nullable=False
    )
    # Polymorphic, no FK — AP: users.id; supplier: vendor_users.id; NULL = system.
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    author_name: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # List of control-plane users.id strings (AP @mentions). NULL for supplier.
    mentions: Mapped[list | None] = mapped_column(JSONB)
    # List of attachment dicts (see backend/docs/supplier-chat.md).
    attachments: Mapped[list | None] = mapped_column(JSONB)
    template_key: Mapped[str | None] = mapped_column(String(50))

    thread: Mapped[SupplierChatThread] = relationship(back_populates="messages")
