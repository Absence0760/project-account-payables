"""Audit logging service — writes a row for every invoice lifecycle event."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import AuditLog


async def log_action(
    db: AsyncSession,
    *,
    correlation_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    details: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        correlation_id=correlation_id,
        organization_id=organization_id,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )
    db.add(entry)
    return entry
