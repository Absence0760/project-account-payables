"""Base classes + shared data types for audit-log shipping adapters.

Adapters accept a batch of already-queried `audit_log` rows (as
`AuditLogRow` dataclasses — deliberately decoupled from the ORM so
Lambda / CLI callers don't need a SQLAlchemy session) and write them to
a WORM-compliant sink.

The contract is narrow on purpose:
- `ship(rows)` — write all rows. Raise on failure. Must be atomic from
  the caller's perspective: either everything in the batch is durable
  or the adapter raised.
- `test_connection()` — cheap liveness probe used at startup + by
  admin-facing test endpoints.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuditLogRow:
    """A snapshot of one `audit_log` row, ready to ship.

    Frozen so callers can pass these through threads / batches without
    worrying about mutation. `tenant_db` is stamped in by the shipper so
    downstream sinks can partition per-tenant without needing to resolve
    the org ID back to a DB name.
    """

    id: uuid.UUID
    tenant_db: str
    organization_id: uuid.UUID
    correlation_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    details: dict | None
    created_at: datetime

    def to_json(self) -> dict:
        """Serialize to a JSON-friendly dict (UUIDs + datetimes as strings)."""
        return {
            "id": str(self.id),
            "tenant_db": self.tenant_db,
            "organization_id": str(self.organization_id),
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "details": self.details,
            "created_at": self.created_at.isoformat(),
        }


class AuditShippingAdapter:
    """Base class for a WORM-compliant audit-log sink.

    Subclass and implement `ship()` and `test_connection()`. Register
    via `@register_audit_shipping_adapter("name")`.
    """

    provider_name: str = "base"

    def __init__(self, config: dict):
        self.config = config

    async def ship(self, rows: list[AuditLogRow]) -> None:
        """Persist `rows` to the sink. Raise on any failure.

        Implementations should be idempotent where possible (replays on
        retry are expected — the shipper marks rows shipped only AFTER
        every configured adapter has succeeded). But non-idempotent
        adapters are acceptable as long as duplicates on the sink side
        are downstream auditors' problem, not ours.
        """
        raise NotImplementedError

    async def test_connection(self) -> bool:
        """Cheap connectivity + config check. Return False on any error."""
        raise NotImplementedError
