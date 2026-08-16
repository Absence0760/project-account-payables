"""Retention-policy enforcement sweep (SOX records management).

Configurable retention periods per record class are stored on
``Organization.settings.retention`` (e.g. ``{"invoices_months": 84,
"audit_log_months": 84}``) — NOT hardcoded. This sweep finds records past their
retention window and ARCHIVES them through a privileged, AUDITED path, writing a
``retention.archived`` audit row per batch.

CRITICAL — it composes with the audit-immutability trigger (migration 0022):
``audit_log`` rows can NOT be deleted (the BEFORE-DELETE trigger rejects every
DELETE). So this sweep NEVER deletes audit rows. For the ``audit_log`` class,
"retention" means *verifying* that rows past the window have been WORM-shipped
(``shipped_at`` set) and recording a retention manifest (counts) in the audit
trail — never deletion. Unshipped-but-overdue rows are surfaced in the manifest
so an operator knows the WORM sink is behind.

For deletable business records we soft-archive terminal-state invoices: an
``invoices`` row in a terminal state (``done`` / ``paid``) whose age exceeds the
``invoices_months`` window gets an ``archived_at`` marker stamped into its
``meta`` JSONB bag. No row is destroyed and no schema change is needed — the
marker is the privileged archival action, fully reversible, and the sweep is
idempotent (already-marked rows are skipped, so a re-run never double-archives).

Mirrors ``contract_renewal`` / ``qms_sync``: a long-lived asyncio loop started
in ``main.lifespan``, fresh per-tenant engine, one tenant's failure logged but
never halting the sweep. Disabled by default (``FEOH_RETENTION_ENABLED``).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.workflow import AuditLog
from app.services.audit_dispatch import dispatch_audit
from app.services.sweep_health import SWEEP_RETENTION, run_sweep_loop

logger = logging.getLogger(__name__)

# Terminal invoice states eligible for soft-archival once past the window. We
# never archive an in-flight invoice — only a closed one (paid out or done).
_ARCHIVABLE_INVOICE_STATES = (InvoiceStatus.done, InvoiceStatus.paid)

# Average days per month for window math. Retention windows are coarse
# (months / years), so a 30.44-day month is plenty precise and avoids a
# calendar-arithmetic dependency.
_DAYS_PER_MONTH = 30.44


@dataclass
class RetentionResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    invoices_archived: int = 0
    audit_rows_overdue: int = 0
    audit_rows_overdue_unshipped: int = 0
    failures: int = 0


def resolve_retention_months(settings_dict: dict | None, record_class: str) -> int:
    """Pure resolver for the effective retention window (months) of a class.

    Looks up ``settings.retention.<record_class>_months`` (the per-org,
    configurable value), falling back to the platform default
    (``FEOH_RETENTION_DEFAULT_MONTHS``). Never raises — a malformed / missing
    value degrades to the default.
    """
    retention = ((settings_dict or {}).get("retention")) or {}
    raw = retention.get(f"{record_class}_months")
    try:
        months = int(raw)
        if months > 0:
            return months
    except (TypeError, ValueError):
        pass
    return settings.retention_default_months


async def sweep_tenant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    settings_dict: dict | None,
    now: datetime | None = None,
) -> RetentionResult:
    """Enforce retention for one tenant. Caller owns the transaction (commits).

    Returns a per-tenant :class:`RetentionResult`. Idempotent: an already-marked
    invoice is skipped, so re-running never double-archives.
    """
    result = RetentionResult(tenants_scanned=1)
    ref_now = now or datetime.now(UTC)

    # --- Business records: soft-archive overdue terminal invoices -----------
    inv_months = resolve_retention_months(settings_dict, "invoices")
    inv_cutoff = ref_now - timedelta(days=inv_months * _DAYS_PER_MONTH)

    candidates = (
        (
            await db.execute(
                select(Invoice).where(
                    Invoice.status.in_(_ARCHIVABLE_INVOICE_STATES),
                    Invoice.created_at < inv_cutoff,
                )
            )
        )
        .scalars()
        .all()
    )

    archived_ids: list[str] = []
    for invoice in candidates:
        meta = dict(invoice.meta or {})
        if meta.get("archived_at"):
            continue  # idempotent: already archived in an earlier sweep
        meta["archived_at"] = ref_now.isoformat()
        invoice.meta = meta
        flag_modified(invoice, "meta")
        archived_ids.append(str(invoice.id))

    result.invoices_archived = len(archived_ids)

    # --- Audit class: verify WORM-shipment, never delete --------------------
    audit_months = resolve_retention_months(settings_dict, "audit_log")
    audit_cutoff = ref_now - timedelta(days=audit_months * _DAYS_PER_MONTH)

    overdue_total = (
        await db.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.created_at < audit_cutoff)
        )
    ).scalar_one()
    overdue_unshipped = (
        await db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.created_at < audit_cutoff,
                AuditLog.shipped_at.is_(None),
            )
        )
    ).scalar_one()
    result.audit_rows_overdue = int(overdue_total)
    result.audit_rows_overdue_unshipped = int(overdue_unshipped)

    # --- Audited manifest of this sweep -------------------------------------
    # Only write a row when the sweep actually did / observed something, so an
    # idle tenant doesn't append a no-op manifest every tick. The details are a
    # PII-free retention manifest (counts + window months + archived ids).
    if archived_ids or overdue_total:
        await dispatch_audit(
            db,
            correlation_id=uuid.uuid4(),
            organization_id=organization_id,
            actor_id=None,  # system sweep, no human actor
            action="retention.archived",
            entity_type="retention",
            entity_id=organization_id,
            details={
                "invoices_months": inv_months,
                "audit_log_months": audit_months,
                "invoices_archived": len(archived_ids),
                "archived_invoice_ids": archived_ids,
                "audit_rows_overdue": int(overdue_total),
                "audit_rows_overdue_unshipped": int(overdue_unshipped),
                "audit_log_note": (
                    "audit_log rows are WORM/immutable and never deleted; "
                    "retention verifies shipment only"
                ),
            },
        )

    return result


async def run_retention_once(*, now: datetime | None = None) -> RetentionResult:
    """One sweep across every tenant. Safe to call directly (CLI / tests)."""
    total = RetentionResult()
    ref_now = now or datetime.now(UTC)

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(
            select(Organization.id, Organization.db_name, Organization.settings)
        )
        tenants = list(rows.all())

    for org_id, db_name, settings_dict in tenants:
        total.tenants_scanned += 1
        engine = None
        try:
            engine = create_async_engine(_make_tenant_url(db_name))
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                tenant_result = await sweep_tenant(
                    db,
                    organization_id=org_id,
                    settings_dict=settings_dict,
                    now=ref_now,
                )
                await db.commit()
            total.invoices_archived += tenant_result.invoices_archived
            total.audit_rows_overdue += tenant_result.audit_rows_overdue
            total.audit_rows_overdue_unshipped += tenant_result.audit_rows_overdue_unshipped
        except Exception as exc:  # noqa: BLE001 — one tenant must not halt the sweep
            logger.warning("[retention] failed sweeping %s: %s", db_name, exc.__class__.__name__)
            total.failures += 1
        finally:
            if engine is not None:
                await engine.dispose()

    if total.invoices_archived or total.audit_rows_overdue or total.failures:
        logger.info(
            "[retention] swept %d tenant(s); archived=%d audit_overdue=%d "
            "audit_overdue_unshipped=%d failed=%d",
            total.tenants_scanned,
            total.invoices_archived,
            total.audit_rows_overdue,
            total.audit_rows_overdue_unshipped,
            total.failures,
        )
    return total


async def run_retention_loop() -> None:
    """Long-lived loop started in ``main.lifespan``; cancelled on shutdown.
    Body is the shared ``sweep_health.run_sweep_loop``."""
    await run_sweep_loop(
        SWEEP_RETENTION,
        lambda: run_retention_once(),
        interval_seconds=settings.retention_interval_seconds,
        log=logger,
        log_prefix="[retention]",
    )
