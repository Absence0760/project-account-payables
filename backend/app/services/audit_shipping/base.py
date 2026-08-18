"""Base classes + shared data types for audit-log shipping adapters.

Adapters accept a batch of already-queried `audit_log` rows (as
`AuditLogRow` dataclasses — deliberately decoupled from the ORM so
Lambda / CLI callers don't need a SQLAlchemy session) and write them to
a WORM-compliant sink.

The contract is narrow on purpose:
- `ship(rows)` — write all rows. Raise on failure. All-or-nothing in the
  SHIPPER'S BOOKKEEPING, at-least-once at the SINK: `shipped_at` is
  stamped only when `ship` returns cleanly, so a raise means the whole
  batch is retried next tick — but rows the sink already accepted before
  the raise stay accepted and arrive twice.

  That is not a shortfall of any one adapter, it is what these sinks
  are. A batch spans several `PutLogEvents` calls (one per
  `(tenant, day)` stream, and more when the batch exceeds the API's 1 MiB
  cap), `FEOH_AUDIT_SHIPPING_PROVIDERS` fans out to several adapters, and
  none of that composes into a transaction — a later call failing cannot
  un-write an earlier one. So this docstring used to promise atomicity
  the code never had, while `ship()` below and `audit_log_shipper` both
  already documented the replay. The reconciliation is on read: every
  shipped event carries the `audit_log` row's own `id`, so a duplicate in
  the WORM store is identifiable rather than a second event. Deliberate:
  a duplicated audit row is recoverable, a MISSING one is not, so the
  retry direction is the safe one.
- `test_connection()` — liveness + WORM-config probe. `app/main.py`'s
  lifespan calls it once per configured adapter when
  `FEOH_AUDIT_SHIPPING_ENABLED` is on and `FEOH_DEBUG` is off, and
  refuses to start the process on a False. There is no admin-facing
  test endpoint for audit shipping; boot is the only caller.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


class AuditShippingRejected(RuntimeError):
    """A sink accepted the call but did NOT ingest every row.

    Distinct from a transport error: the API returned success, so nothing else
    would have noticed. CloudWatch's ``PutLogEvents`` is the case this exists
    for — it answers 200 with a ``rejectedLogEventsInfo`` block naming the
    events it silently dropped (too old for the log group's retention, too far
    in the future). Swallowing that would stamp ``shipped_at`` on rows that
    never reached the WORM store, which is precisely the "SOC 2 evidence
    reading green with nothing behind it" failure the boot-time
    ``test_connection`` probe already refuses to allow.

    Raising keeps the rows unshipped, so the next tick retries, the sweep's
    consecutive-failure streak climbs (``GET /api/health/sweeps``), and the
    retention manifest's ``audit_rows_overdue_unshipped`` counts them.
    """


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
        every configured adapter has succeeded, and a raise part-way
        through a multi-call batch leaves the already-accepted part
        accepted). But non-idempotent adapters are acceptable as long as
        duplicates on the sink side are downstream auditors' problem, not
        ours — every event carries the `audit_log` row's `id`, so a
        replay is identifiable on read. See the module docstring.
        """
        raise NotImplementedError

    async def test_connection(self) -> bool:
        """Connectivity + WORM-config check. Return False on any error.

        Called from the lifespan boot guard, which refuses to start the
        process when this returns False — so an adapter must return False
        (not True, not raise) for any condition that would make its ship
        untrustworthy as tamper-evident evidence.
        """
        raise NotImplementedError
