"""Extraction dispatch — routes extraction to local background task or SQS/Lambda.

Local mode uses a single worker thread with a job queue so that extractions
run one at a time.  This prevents concurrent uploads from triggering parallel
AI API calls that get rate-limited (429) and fail.
"""

import asyncio
import json
import logging
import queue
import threading
import uuid

import boto3

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local extraction queue — a small pool of worker threads processes jobs
# concurrently (default 3).  Keeps throughput high for bulk uploads while
# staying under typical AI-provider rate limits.
# ---------------------------------------------------------------------------

_WORKER_COUNT = 3
_job_queue: queue.Queue[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = queue.Queue()
_worker_threads: list[threading.Thread] = []
_worker_lock = threading.Lock()


def _ensure_workers() -> None:
    """Start worker threads, replacing any that have exited."""
    with _worker_lock:
        # Remove dead threads
        _worker_threads[:] = [t for t in _worker_threads if t.is_alive()]
        # Top up to _WORKER_COUNT
        while len(_worker_threads) < _WORKER_COUNT:
            t = threading.Thread(target=_extraction_worker, daemon=True)
            t.start()
            _worker_threads.append(t)


def _extraction_worker() -> None:
    """Drain the job queue, processing one extraction at a time.

    Exits after 120 s of idle time so we don't keep a thread alive when
    there's no upload activity.  The next ``dispatch_extraction`` call
    will restart it.
    """
    logger.info("[extraction] Worker thread started")
    while True:
        try:
            invoice_id, org_id, actor_id = _job_queue.get(timeout=120)
        except queue.Empty:
            logger.info("[extraction] Worker idle — exiting")
            break  # idle timeout — exit

        logger.info("[extraction] Worker picked up job for invoice %s", invoice_id)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_local(invoice_id, org_id, actor_id))
        except Exception as exc:
            # Class name only — a pipeline exception can carry extracted PII.
            logger.error(
                "[extraction] Unexpected error for invoice %s: %s",
                invoice_id,
                exc.__class__.__name__,
            )
            try:
                loop.run_until_complete(
                    _mark_failed(invoice_id, org_id, "Extraction crashed unexpectedly")
                )
            except Exception as mark_exc:
                logger.error(
                    "[extraction] Failed to mark invoice %s failed after crash: %s",
                    invoice_id,
                    mark_exc.__class__.__name__,
                )
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def dispatch_extraction(
    invoice_id: uuid.UUID,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Trigger extraction via the configured mode (local or lambda)."""
    if settings.extraction_mode == "lambda":
        _send_to_sqs(invoice_id, org_id, actor_id)
    else:
        _job_queue.put((invoice_id, org_id, actor_id))
        _ensure_workers()


# ---------------------------------------------------------------------------
# SQS path (Lambda mode)
# ---------------------------------------------------------------------------


def _send_to_sqs(
    invoice_id: uuid.UUID,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Put extraction job on SQS for Lambda to pick up."""
    client = boto3.client(
        "sqs",
        endpoint_url=settings.aws_endpoint_url or settings.s3_endpoint_url,  # LocalStack when set
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
    )
    client.send_message(
        QueueUrl=settings.sqs_extraction_queue_url,
        MessageBody=json.dumps(
            {
                "invoice_id": str(invoice_id),
                "org_id": str(org_id),
                "actor_id": str(actor_id),
            }
        ),
        MessageGroupId=str(invoice_id),
    )


# ---------------------------------------------------------------------------
# Local execution
# ---------------------------------------------------------------------------


async def _run_local(
    invoice_id: uuid.UUID,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Run extraction in-process with its own DB session and engine.

    This runs in a background thread with its own event loop,
    so we must create fresh engines (not reuse cached ones from the main loop).
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import _make_tenant_url
    from app.models.invoice import Invoice
    from app.models.organization import Organization
    from app.services.extraction import run_extraction

    # Create a fresh control-plane engine for this thread.
    # Minimal pool (1 conn) — each worker only runs one job at a time and
    # we don't want 3 workers × 2 engines × default pool (15) to exhaust
    # PostgreSQL's max_connections (100).
    ctrl_engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
    ctrl_factory = async_sessionmaker(ctrl_engine, expire_on_commit=False)

    try:
        # Keep ctrl_db open for the whole extraction — run_extraction needs it
        # for ExtractionUsage tracking (control-plane table).
        async with ctrl_factory() as ctrl_db:
            result = await ctrl_db.execute(select(Organization).where(Organization.id == org_id))
            org = result.scalar_one_or_none()
            if not org:
                logger.warning("[extraction] Organization %s not found", org_id)
                return

            # Create a fresh tenant engine for this thread
            tenant_url = _make_tenant_url(org.db_name)
            tenant_engine = create_async_engine(tenant_url, pool_size=1, max_overflow=0)
            tenant_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

            try:
                async with tenant_factory() as db:
                    try:
                        result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
                        invoice = result.scalar_one_or_none()
                        if not invoice:
                            logger.warning("[extraction] Invoice %s not found", invoice_id)
                            return

                        logger.info(
                            "[extraction] Starting extraction for invoice %s, file_key=%s",
                            invoice_id,
                            invoice.file_key,
                        )
                        await run_extraction(
                            db,
                            invoice,
                            actor_id=actor_id,
                            org_settings=org.settings,
                            ctrl_db=ctrl_db,
                        )
                        logger.info(
                            "[extraction] Completed extraction for invoice %s, status=%s",
                            invoice_id,
                            invoice.status,
                        )
                    except Exception as exc:
                        # Class name only — never the raw message. An extraction
                        # exception (vision/OCR SDK, GL validation, dup-detect)
                        # can carry invoice PII (vendor tax id / bank / address),
                        # and no log-redaction filter is installed here.
                        logger.error(
                            "[extraction] ERROR for invoice %s: %s",
                            invoice_id,
                            exc.__class__.__name__,
                        )
                        await db.rollback()
                        # Ensure the invoice doesn't stay stuck in 'pending' —
                        # run_extraction's own error handler transitions to 'failed',
                        # but if *that* handler itself blew up we need a fallback.
                        await _fail_invoice_safely(db, invoice_id, actor_id, str(exc))
            finally:
                await tenant_engine.dispose()
    finally:
        await ctrl_engine.dispose()


async def _fail_invoice_safely(
    db, invoice_id: uuid.UUID, actor_id: uuid.UUID | None, error: str
) -> None:
    """Best-effort transition to 'failed' if the invoice is still in 'pending'.

    Called as a fallback when ``run_extraction``'s own error handler raised.
    Swallows all exceptions so it never disrupts the caller's cleanup path.
    """
    try:
        from sqlalchemy import select

        from app.models.invoice import Invoice, InvoiceStatus
        from app.services.workflow_engine import transition_invoice

        result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
        inv = result.scalar_one_or_none()
        if inv and inv.status == InvoiceStatus.pending:
            await transition_invoice(
                db,
                inv,
                InvoiceStatus.failed,
                actor_id=actor_id,
                action_name="invoice.extraction_failed_fallback",
                details={"error": error},
            )
            await db.commit()
            logger.info("[extraction] Fallback: marked invoice %s as failed", invoice_id)
    except Exception as exc:
        logger.error(
            "[extraction] Fallback fail-invoice path errored for invoice %s: %s",
            invoice_id,
            exc.__class__.__name__,
        )


async def _mark_failed(invoice_id: uuid.UUID, org_id: uuid.UUID, reason: str) -> None:
    """Transition an invoice to 'failed' using a fresh DB session.

    Used by the worker when a job times out or crashes outside ``_run_local``.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import _make_tenant_url
    from app.models.invoice import Invoice, InvoiceStatus
    from app.models.organization import Organization

    ctrl_engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
    ctrl_factory = async_sessionmaker(ctrl_engine, expire_on_commit=False)
    try:
        async with ctrl_factory() as ctrl_db:
            result = await ctrl_db.execute(select(Organization).where(Organization.id == org_id))
            org = result.scalar_one_or_none()
            if not org:
                return

        tenant_url = _make_tenant_url(org.db_name)
        tenant_engine = create_async_engine(tenant_url, pool_size=1, max_overflow=0)
        try:
            tenant_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)
            async with tenant_factory() as db:
                from app.services.workflow_engine import transition_invoice

                result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
                invoice = result.scalar_one_or_none()
                if invoice and invoice.status == InvoiceStatus.pending:
                    await transition_invoice(
                        db,
                        invoice,
                        InvoiceStatus.failed,
                        actor_id=None,
                        action_name="invoice.extraction_timed_out",
                        details={"reason": reason},
                    )
                    await db.commit()
                    logger.info("[extraction] Marked invoice %s as failed: %s", invoice_id, reason)
        finally:
            await tenant_engine.dispose()
    except Exception as exc:
        logger.error(
            "[extraction] _mark_failed errored for invoice %s: %s",
            invoice_id,
            exc.__class__.__name__,
        )
    finally:
        await ctrl_engine.dispose()
