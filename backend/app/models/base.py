import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantMixin:
    """Every tenant-scoped table gets an organization_id FK."""

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )


class EntityMixin:
    """A business table that belongs to a legal entity (subsidiary) within the
    tenant. ``entity_id`` is a nullable FK to the tenant-local ``entities``
    table. Migration 0029 backfills existing rows to the tenant's default
    entity; ``GLAccount`` is the deliberate exception — a NULL ``entity_id``
    there means the account is *shared* across every entity (see
    ``docs/multi-entity.md``).

    Distinct from ``AuditLog.entity_id`` / ``Notification.entity_id``, which
    identify the audited / notified row, not a subsidiary. ``declared_attr`` is
    required (not a plain class attribute like ``TenantMixin``) so each table
    gets its own ``ForeignKey`` / column instance.
    """

    @declared_attr
    def entity_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(
            UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True, index=True
        )
