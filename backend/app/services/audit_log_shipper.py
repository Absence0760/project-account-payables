"""Background shipper for centralized, WORM-compliant audit-log storage.

Every tick:

  1. Enumerate tenant DBs from the control plane.
  2. For each tenant, SELECT up to `FEOH_AUDIT_SHIPPING_BATCH_SIZE`
     `audit_log` rows with `shipped_at IS NULL`, oldest first.
  3. Fan the batch out to every configured adapter (CloudWatch, S3, …).
     All adapters must succeed before we mark the rows shipped.
  4. On success, stamp `shipped_at = now()` in a single UPDATE.
  5. On failure, log a WARNING with the tenant + row count and leave
     `shipped_at` NULL so the next tick retries.

SOC 2 control — the `audit_log` table is per-tenant and lives inside the
customer's tenant DB, where a determined admin could theoretically edit
rows. Shipping them out to CloudWatch Logs (append-only) and S3 Object
Lock (immutable for the retention period) gives auditors an independent
tamper-evident copy.

Mirrors the `extraction_reaper` pattern: long-lived asyncio task started
in `main.lifespan`, per-tenant fresh engine, tenant failures logged but
don't halt the sweep. No thread pool — SQLAlchemy async + `asyncio.to_thread`
for the boto3 calls is all the concurrency we need.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.organization import Organization
from app.models.workflow import AuditLog
from app.services.audit_shipping import (
    AuditLogRow,
    AuditShippingAdapter,
    get_audit_shipping_adapters,
)

logger = logging.getLogger(__name__)


@dataclass
class ShipResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    rows_shipped: int = 0
    failures: int = 0  # tenant DBs that raised; rows stay unshipped


def _parse_providers(raw: str) -> list[str]:
    """Split the comma-separated env var into clean adapter names."""
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def _build_adapters() -> list[AuditShippingAdapter]:
    """Instantiate every adapter listed in FEOH_AUDIT_SHIPPING_PROVIDERS.

    Split out so tests can patch it. Returns [] if nothing is configured,
    in which case the shipper skips the tick entirely.
    """
    providers = _parse_providers(settings.audit_shipping_providers)
    if not providers:
        return []
    return get_audit_shipping_adapters(providers, config={})


async def ship_once(*, adapters: list[AuditShippingAdapter] | None = None) -> ShipResult:
    """One sweep across every tenant. Safe to call directly (e.g. from a CLI).

    `adapters=None` builds adapters from settings. Pass an explicit list
    from tests or from a CLI that wants to override provider selection.
    """
    result = ShipResult()

    if adapters is None:
        adapters = _build_adapters()
    if not adapters:
        # Nothing to ship to — short-circuit. The shipper still honours its
        # interval so flipping `audit_shipping_providers` at runtime works.
        return result

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(select(Organization.id, Organization.db_name))
        tenants = list(rows.all())

    for _org_id, db_name in tenants:
        result.tenants_scanned += 1
        try:
            shipped = await _ship_tenant(db_name, adapters)
            result.rows_shipped += shipped
        except Exception as exc:
            # One tenant's outage / bad config shouldn't halt the sweep.
            # Log + move on; rows stay unshipped and next tick retries.
            # Class only, not the message (PII-out-of-logs invariant).
            logger.warning("[audit-shipper] failed to ship %s: %s", db_name, exc.__class__.__name__)
            result.failures += 1

    if result.rows_shipped or result.failures:
        logger.info(
            "[audit-shipper] swept %d tenant(s); shipped=%d failed_sweeps=%d",
            result.tenants_scanned,
            result.rows_shipped,
            result.failures,
        )
    return result


async def _ship_tenant(db_name: str, adapters: list[AuditShippingAdapter]) -> int:
    """Pull one batch of unshipped rows from `db_name` and ship them.

    Uses a fresh engine per call — same pattern as the extraction reaper.
    The shipper runs in the FastAPI event loop and tenant engines aren't
    cached for it; cheaper to spin one up than to plumb through the cache.

    Returns the count of rows successfully shipped + marked. Raises on
    adapter failure so `ship_once` counts the sweep as failed.
    """
    batch_size = settings.audit_shipping_batch_size
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as db:
            rows_orm = (
                (
                    await db.execute(
                        select(AuditLog)
                        .where(AuditLog.shipped_at.is_(None))
                        .order_by(AuditLog.created_at.asc())
                        .limit(batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows_orm:
                return 0

            batch = [
                AuditLogRow(
                    id=r.id,
                    tenant_db=db_name,
                    organization_id=r.organization_id,
                    correlation_id=r.correlation_id,
                    actor_id=r.actor_id,
                    action=r.action,
                    entity_type=r.entity_type,
                    entity_id=r.entity_id,
                    details=r.details,
                    created_at=r.created_at,
                )
                for r in rows_orm
            ]

            # Fan out to every adapter. A single adapter failing raises
            # — none of the rows get marked shipped, so the next tick
            # retries the entire batch. The mock adapter dedups a bit
            # via unique id, and CloudWatch / S3 have at-least-once
            # semantics anyway, so replays are acceptable.
            for adapter in adapters:
                await adapter.ship(batch)

            # Single UPDATE stamping shipped_at. Use ids to be explicit —
            # the SELECT could have returned rows that a concurrent
            # rollback cleaned up, but that's a stretch given audit rows
            # are write-once.
            ids = [r.id for r in rows_orm]
            now = datetime.now(UTC)
            await db.execute(update(AuditLog).where(AuditLog.id.in_(ids)).values(shipped_at=now))
            await db.commit()
            logger.info("[audit-shipper] %s: shipped %d row(s)", db_name, len(ids))
            return len(ids)
    finally:
        await engine.dispose()


async def run_shipper_loop() -> None:
    """Long-lived loop, started in `main.lifespan` on app startup and
    cancelled on shutdown. Sleeps `FEOH_AUDIT_SHIPPING_INTERVAL_SECONDS`
    between sweeps.
    """
    interval = settings.audit_shipping_interval_seconds
    providers = _parse_providers(settings.audit_shipping_providers)
    logger.info(
        "[audit-shipper] started; interval=%ds batch=%d providers=%s",
        interval,
        settings.audit_shipping_batch_size,
        providers or "(none)",
    )
    try:
        while True:
            try:
                await ship_once()
            except Exception as exc:
                # Catch-all so one bad sweep doesn't kill the loop. Logged
                # at ERROR so it surfaces without taking the app down.
                # Class only, not the message (PII-out-of-logs invariant).
                logger.error(
                    "[audit-shipper] sweep raised: %s", exc.__class__.__name__, exc_info=True
                )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[audit-shipper] shutting down")
        raise
