"""Pydantic schemas for the auditor-export endpoints (`/api/audit`)."""

from __future__ import annotations

from pydantic import BaseModel


class AuditExportEntry(BaseModel):
    """One flattened audit row for auditor export.

    Mirrors ``AuditLogEntryResponse`` but resolves the actor email alongside
    the name and is shaped for CSV/JSON export. ``details`` carries the
    sanitised change diff / field-name list written at audit time — it never
    contains a regulated value (tax id, bank number, PAN), so this schema is
    safe to emit on the HTTP surface.
    """

    id: str
    correlation_id: str
    actor_id: str | None
    actor_name: str | None = None
    actor_email: str | None = None
    action: str
    entity_type: str
    entity_id: str | None
    details: dict | None
    created_at: str

    @classmethod
    def from_db(
        cls,
        entry,
        actor_names: dict[str, str] | None = None,
        actor_emails: dict[str, str] | None = None,
    ) -> AuditExportEntry:
        actor_id_str = str(entry.actor_id) if entry.actor_id else None
        return cls(
            id=str(entry.id),
            correlation_id=str(entry.correlation_id) if entry.correlation_id else "",
            actor_id=actor_id_str,
            actor_name=(actor_names or {}).get(actor_id_str) if actor_id_str else None,
            actor_email=(actor_emails or {}).get(actor_id_str) if actor_id_str else None,
            action=entry.action,
            entity_type=entry.entity_type,
            entity_id=str(entry.entity_id) if entry.entity_id else None,
            details=entry.details,
            created_at=entry.created_at.isoformat() if entry.created_at else "",
        )
