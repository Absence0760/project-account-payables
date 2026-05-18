"""Reaper for invoices stuck in `pending` extraction.

When extraction never completes — Ollama hung, worker crashed, an upload
landed without a file_key — the invoice sits in `pending` forever, taking
up a slot in the reviewer's queue and confusing the user. This service
sweeps every tenant DB on a timer and transitions any `pending` invoice
older than `AP_EXTRACTION_TIMEOUT_SECONDS` to `failed`. The reviewer can
then re-trigger extraction or fall back to manual entry.

Pure async — runs as a long-lived asyncio task started in `main.lifespan`.
Cancelling the task (server shutdown) is handled cleanly.

Also exposed as a CLI: `python scripts/reap_stuck_extractions.py`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization

logger = logging.getLogger(__name__)


@dataclass
class ReapResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    invoices_reaped: int = 0
    failures: int = 0  # tenant DBs we couldn't reach


async def reap_once(*, threshold_seconds: int | None = None) -> ReapResult:
    """One sweep across every tenant. Safe to call directly from the CLI.

    `threshold_seconds=None` uses the configured default. Passing an
    explicit value is useful from the CLI for one-shot cleanup with a
    tighter / looser cutoff than production.
    """
    threshold = threshold_seconds or settings.extraction_timeout_seconds
    cutoff = datetime.now(UTC) - timedelta(seconds=threshold)
    result = ReapResult()

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(select(Organization.id, Organization.db_name))
        tenants = list(rows.all())

    for _org_id, db_name in tenants:
        result.tenants_scanned += 1
        try:
            reaped = await _reap_tenant(db_name, cutoff)
            result.invoices_reaped += reaped
        except Exception as exc:
            # Don't let one tenant's DB outage halt the sweep — log and move on.
            logger.warning("[reaper] failed to sweep %s: %s", db_name, exc)
            result.failures += 1

    if result.invoices_reaped or result.failures:
        logger.info(
            "[reaper] swept %d tenant(s); reaped=%d failed_sweeps=%d",
            result.tenants_scanned,
            result.invoices_reaped,
            result.failures,
        )
    return result


async def _reap_tenant(db_name: str, cutoff: datetime) -> int:
    """Transition stuck `pending` invoices in one tenant DB to `failed`.

    Uses a fresh engine per call — same pattern as `extraction_dispatch._run_local`.
    The reaper runs in the FastAPI event loop, but tenant engines aren't
    cached for it; cheaper to spin one up than to plumb through the cache.
    """
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    reaped = 0

    try:
        async with factory() as db:
            stuck = (
                (
                    await db.execute(
                        select(Invoice).where(
                            Invoice.status == InvoiceStatus.pending,
                            Invoice.created_at < cutoff,
                        )
                    )
                )
                .scalars()
                .all()
            )

            from app.services.workflow_engine import transition_invoice

            for inv in stuck:
                age = int((datetime.now(UTC) - inv.created_at).total_seconds())
                # Route the system transition through transition_invoice so
                # the SOC 2 audit-shipping pipeline captures the row, same
                # as every other status change. `actor_id=None` marks it as
                # a system action; the audit row's `action` distinguishes
                # reaper sweeps from user-driven failures.
                await transition_invoice(
                    db,
                    inv,
                    InvoiceStatus.failed,
                    actor_id=None,
                    action_name="invoice.extraction_reaped",
                    details={"age_seconds": age, "threshold_seconds": int(cutoff.timestamp())},
                )
                # The warnings array stays — it's the reviewer-facing surface
                # (visible in the row drawer); the audit row is the
                # auditor-facing one. Both serve different SOC 2 readers.
                warnings = list(inv.warnings or [])
                warnings.append(
                    {
                        "type": "extraction_timeout",
                        "severity": "error",
                        "message": (
                            f"Extraction stuck in 'pending' for >{age}s; "
                            "auto-transitioned to 'failed' by the reaper. "
                            "Re-trigger extraction or fall back to manual entry."
                        ),
                    }
                )
                inv.warnings = warnings
                reaped += 1

            if reaped:
                await db.commit()
                logger.info("[reaper] %s: reaped %d stuck invoice(s)", db_name, reaped)
    finally:
        await engine.dispose()

    return reaped


async def run_reaper_loop() -> None:
    """Long-lived loop. Started in `main.lifespan` on app startup, cancelled
    on shutdown. Sleeps `AP_EXTRACTION_REAPER_INTERVAL` between sweeps so
    even a crashed worker only leaves an invoice stuck for the threshold +
    one interval at most.
    """
    interval = settings.extraction_reaper_interval_seconds
    logger.info(
        "[reaper] started; threshold=%ds interval=%ds",
        settings.extraction_timeout_seconds,
        interval,
    )
    try:
        while True:
            try:
                await reap_once()
            except Exception as exc:
                # Catch-all so one bad sweep doesn't kill the loop. Logs at
                # ERROR so it's noticeable but doesn't take the app down.
                logger.error("[reaper] sweep raised: %s", exc, exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[reaper] shutting down")
        raise
