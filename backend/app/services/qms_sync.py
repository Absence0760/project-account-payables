"""QMS sync — pull external quality inspections into ``quality_inspections``.

Two entry points:

  * :func:`sync_tenant_inspections` — one tenant, one sweep. Fetches records
    from the configured QMS adapter, resolves each record's ``po_number`` /
    ``gr_number`` to local ``PurchaseOrder`` / ``GoodsReceipt`` ids, then
    UPSERTS a :class:`~app.models.quality_inspection.QualityInspection`
    idempotently keyed on ``(organization_id, inspection_number)``. Writes an
    append-only ``quality_inspection.synced`` audit row per landed record. The
    caller owns the transaction (commits) — same contract as the inspections
    router, which runs inside ``get_tenant_db``'s commit/rollback wrapper.

  * :func:`run_qms_sync_loop` — the background sweep. Enumerates every tenant
    DB from the control plane and runs :func:`sync_tenant_inspections` for each
    org that has a QMS configured. Mirrors ``contract_renewal.run_renewal_loop``:
    long-lived asyncio task started in ``main.lifespan``, fresh per-tenant
    engine, one tenant's failure logged but never halting the sweep. Disabled
    by default (``FEOH_QMS_SYNC_ENABLED``).

Idempotency: the upsert key is ``(organization_id, inspection_number)``. A
re-run updates the existing row's result / quantities / notes / dates in place
rather than inserting a duplicate — exactly the ``status``-guard role the other
sweeps rely on, but here it's a natural-key lookup. ``inspector`` and the QMS
record carry no PII we log (the audit ``details`` records the inspection number
and resolution outcome only).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.organization import Organization
from app.models.procurement import GoodsReceipt, PurchaseOrder
from app.models.quality_inspection import QualityInspection
from app.schemas.inspection import VALID_RESULTS
from app.services.audit_dispatch import dispatch_audit
from app.services.qms_adapters import get_qms_adapter
from app.services.qms_adapters.base import QMSInspectionRecord
from app.services.sweep_health import SWEEP_QMS_SYNC, run_sweep_loop
from app.tenant import resolve_default_entity_id

logger = logging.getLogger(__name__)


@dataclass
class QMSSyncResult:
    """Per-tenant sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    fetched: int = 0
    created: int = 0
    updated: int = 0
    failures: int = 0


async def _resolve_po_id(
    db: AsyncSession, org_id: uuid.UUID, po_number: str | None
) -> uuid.UUID | None:
    if not po_number:
        return None
    # po_number is NOT unique (it can repeat across vendors / entities), so cap
    # the lookup at one deterministic row — newest first — rather than
    # `scalar_one_or_none()`, which raises MultipleResultsFound on a duplicate
    # number and fails the whole tenant sweep (every inspection lost).
    return (
        await db.execute(
            select(PurchaseOrder.id)
            .where(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.po_number == po_number,
            )
            .order_by(PurchaseOrder.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _resolve_gr_id(
    db: AsyncSession, org_id: uuid.UUID, gr_number: str | None
) -> uuid.UUID | None:
    if not gr_number:
        return None
    # gr_number is NOT unique either — same deterministic single-row cap as the
    # PO resolver above (a duplicate gr_number must not crash the sweep).
    return (
        await db.execute(
            select(GoodsReceipt.id)
            .where(
                GoodsReceipt.organization_id == org_id,
                GoodsReceipt.gr_number == gr_number,
            )
            .order_by(GoodsReceipt.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def sync_tenant_inspections(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    qms_config: dict | None,
    entity_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> dict:
    """Pull inspections from the configured QMS and upsert them for one tenant.

    Returns ``{"fetched": int, "created": int, "updated": int}``. The caller
    owns the transaction — this flushes but does not commit (so a request-path
    call commits via the dependency wrapper and the background sweep commits
    explicitly).
    """
    adapter = get_qms_adapter(qms_config)
    records: list[QMSInspectionRecord] = await adapter.fetch_inspections()

    if entity_id is None:
        entity_id = await resolve_default_entity_id(db)

    created = 0
    updated = 0
    for rec in records:
        result = rec.result if rec.result in VALID_RESULTS else "pass"
        po_id = await _resolve_po_id(db, org_id, rec.po_number)
        gr_id = await _resolve_gr_id(db, org_id, rec.gr_number)

        existing = (
            await db.execute(
                select(QualityInspection).where(
                    QualityInspection.organization_id == org_id,
                    QualityInspection.inspection_number == rec.inspection_number,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            row = QualityInspection(
                inspection_number=rec.inspection_number,
                po_id=po_id,
                gr_id=gr_id,
                result=result,
                inspected_date=rec.inspected_date,
                inspector=rec.inspector,
                accepted_quantity=rec.accepted_quantity,
                rejected_quantity=rec.rejected_quantity,
                deviation_notes=rec.deviation_notes,
                status="completed",
                organization_id=org_id,
                entity_id=entity_id,
            )
            db.add(row)
            created += 1
            change = "created"
        else:
            row = existing
            row.result = result
            row.inspected_date = rec.inspected_date
            row.inspector = rec.inspector
            row.accepted_quantity = rec.accepted_quantity
            row.rejected_quantity = rec.rejected_quantity
            row.deviation_notes = rec.deviation_notes
            # Backfill document links if the QMS now references docs that
            # exist locally (e.g. the PO/GR was imported after the first sync).
            if po_id is not None:
                row.po_id = po_id
            if gr_id is not None:
                row.gr_id = gr_id
            updated += 1
            change = "updated"

        await db.flush()  # populate row.id for the audit entity_id

        # PII-free audit details — the inspection number + resolution outcome
        # only. Inspector identity and quantities are never surfaced as audit
        # values.
        await dispatch_audit(
            db,
            correlation_id=uuid.uuid4(),
            organization_id=org_id,
            actor_id=actor_id,
            action="quality_inspection.synced",
            entity_type="quality_inspection",
            entity_id=row.id,
            details={
                "inspection_number": rec.inspection_number,
                "result": result,
                "change": change,
                "po_resolved": po_id is not None,
                "gr_resolved": gr_id is not None,
                "provider": adapter.provider_name,
            },
        )

    # Best-effort: re-run matching for invoices touched by the synced
    # inspections so a fresh quality outcome re-gates the 4-way match. A
    # QualityInspection links to a PO/GR, not directly to an invoice, so we
    # rematch invoices that reference the affected PO numbers. Never fails the
    # sync.
    await _best_effort_rematch(db, org_id, records)

    return {"fetched": len(records), "created": created, "updated": updated}


async def _best_effort_rematch(
    db: AsyncSession, org_id: uuid.UUID, records: list[QMSInspectionRecord]
) -> None:
    """Re-run PO matching for invoices whose PO a synced inspection touched.

    Runs inside a SAVEPOINT so any failure (a matching edge case, or — as seen
    on a schema-drifted tenant — a query against a column that hasn't been
    migrated yet) rolls back only the rematch, leaving the already-landed
    inspections + audit rows intact on the outer transaction. The inspections
    are the contract; the rematch is a courtesy that the next invoice mutation
    will redo anyway. Never fails the sync.
    """
    po_numbers = {r.po_number for r in records if r.po_number}
    if not po_numbers:
        return
    try:
        from app.models.invoice import Invoice
        from app.services.invoice_warnings import refresh_warnings

        async with db.begin_nested():
            invoices = (
                (
                    await db.execute(
                        select(Invoice).where(
                            Invoice.organization_id == org_id,
                            Invoice.po_number.in_(po_numbers),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for inv in invoices:
                await refresh_warnings(db, inv)
    except Exception as exc:  # noqa: BLE001 — rematch is advisory, never fatal
        logger.warning(
            "[qms-sync] best-effort rematch skipped for org=%s: %s",
            org_id,
            exc.__class__.__name__,
        )


async def _org_qms_config(settings_blob: dict | None) -> dict | None:
    """Extract the ``qms`` block from an org's settings JSONB (or None)."""
    if isinstance(settings_blob, dict):
        qms = settings_blob.get("qms")
        if isinstance(qms, dict):
            return qms
    return None


async def run_qms_sync_once(*, since: datetime | None = None) -> QMSSyncResult:
    """One sync sweep across every tenant. Safe to call directly (CLI / tests).

    Only orgs with a ``qms`` block in their settings (or the platform default
    provider) are synced; an org with no QMS configured and the default
    provider being ``mock`` would otherwise pull the mock fixtures into every
    tenant, so the sweep skips orgs that have not opted in unless the platform
    provider has been set to something other than ``mock``.
    """
    result = QMSSyncResult()

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(
            select(Organization.id, Organization.db_name, Organization.settings)
        )
        tenants = list(rows.all())

    for org_id, db_name, settings_blob in tenants:
        qms_config = await _org_qms_config(settings_blob)
        # Skip orgs that have not opted in. A platform provider override
        # (FEOH_QMS_PROVIDER != "mock") opts every org in with the default config.
        if qms_config is None:
            if settings.qms_provider == "mock":
                continue
            qms_config = {"provider": settings.qms_provider}

        result.tenants_scanned += 1
        try:
            summary = await _sweep_tenant(db_name, org_id, qms_config)
            result.fetched += summary["fetched"]
            result.created += summary["created"]
            result.updated += summary["updated"]
        except Exception as exc:  # noqa: BLE001 — one tenant must not halt the sweep
            logger.warning("[qms-sync] failed sweeping %s: %s", db_name, exc.__class__.__name__)
            result.failures += 1

    if result.created or result.updated or result.failures:
        logger.info(
            "[qms-sync] swept %d tenant(s); fetched=%d created=%d updated=%d failed_sweeps=%d",
            result.tenants_scanned,
            result.fetched,
            result.created,
            result.updated,
            result.failures,
        )
    return result


async def _sweep_tenant(db_name: str, org_id: uuid.UUID, qms_config: dict) -> dict:
    """Sync one tenant on its own short-lived engine; commits on success."""
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            summary = await sync_tenant_inspections(db, org_id=org_id, qms_config=qms_config)
            await db.commit()
            return summary
    finally:
        await engine.dispose()


async def run_qms_sync_loop() -> None:
    """Long-lived loop started in ``main.lifespan``; cancelled on shutdown.
    Body is the shared ``sweep_health.run_sweep_loop``."""
    await run_sweep_loop(
        SWEEP_QMS_SYNC,
        lambda: run_qms_sync_once(),
        interval_seconds=settings.qms_sync_interval_seconds,
        log=logger,
        log_prefix="[qms-sync]",
    )
