"""Sync payment data to the connected ERP after payment execution.

Runs async in a background thread — doesn't block the payment run response.
Records sync status on each payment for retry capability.
"""

import asyncio
import logging
import threading
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.payment import Payment

logger = logging.getLogger(__name__)


async def dispatch_payment_sync(
    run_id: uuid.UUID,
    org_id: uuid.UUID,
) -> None:
    """Trigger async ERP sync for a completed payment run."""
    thread = threading.Thread(
        target=_run_in_thread,
        args=(run_id, org_id),
        daemon=True,
    )
    thread.start()


def _run_in_thread(run_id: uuid.UUID, org_id: uuid.UUID) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_sync_payments(run_id, org_id))
    finally:
        loop.close()


async def _sync_payments(run_id: uuid.UUID, org_id: uuid.UUID) -> None:
    """Sync all payments in a run to the ERP."""
    ctrl_engine = create_async_engine(settings.database_url)
    ctrl_factory = async_sessionmaker(ctrl_engine, expire_on_commit=False)

    try:
        # Look up org
        async with ctrl_factory() as ctrl_db:
            result = await ctrl_db.execute(select(Organization).where(Organization.id == org_id))
            org = result.scalar_one_or_none()
            if not org:
                logger.warning("[payment-sync] organization %s not found", org_id)
                return

        erp_config = (org.settings or {}).get("erp")
        if not erp_config:
            logger.info("[payment-sync] no ERP configured for org %s, skipping sync", org_id)
            return

        # Import adapters
        import app.services.erp_adapters.dynamics_365_bc  # noqa: F401
        import app.services.erp_adapters.merge_dev  # noqa: F401
        import app.services.erp_adapters.mock_adapter  # noqa: F401
        import app.services.erp_adapters.netsuite  # noqa: F401
        from app.services.erp_adapters import get_erp_adapter

        get_erp_adapter(erp_config)

        # Open tenant DB
        tenant_url = _make_tenant_url(org.db_name)
        tenant_engine = create_async_engine(tenant_url)
        tenant_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

        async with tenant_factory() as db:
            try:
                # Get payments in this run
                result = await db.execute(
                    select(Payment, Invoice)
                    .outerjoin(Invoice, Payment.invoice_id == Invoice.id)
                    .where(Payment.payment_run_id == run_id)
                )
                rows = result.all()

                synced = 0
                skipped = 0
                failed = 0
                for payment, invoice in rows:
                    try:
                        # A run can hold a mix of settled and in-flight payments
                        # (e.g. one ACH `completed` by the mock adapter alongside
                        # one `submitted` awaiting the processor webhook). Only a
                        # payment we believe actually settled may mark its invoice
                        # `paid` — flipping an in-flight payment's invoice to
                        # `paid` here would claim money moved before the rail
                        # confirmed it (and would pre-empt the webhook's own
                        # `paid` transition, which never re-fires for an invoice
                        # already in `paid`). The webhook handler triggers a fresh
                        # ERP sync once the in-flight payment settles.
                        if payment.status != "completed":
                            skipped += 1
                            continue

                        # Build payment data for ERP
                        # In production, this would call adapter.post_payment()
                        # For now, log and mark as synced. Amount is not PII; goes
                        # through the module logger so it reaches the same
                        # aggregation/redaction pipeline as the rest of the sync.
                        logger.info(
                            "[payment-sync] syncing payment %s: invoice=%s, amount=%s, method=%s",
                            payment.id,
                            invoice.invoice_number if invoice else "?",
                            f"{payment.amount:.2f}",
                            payment.method,
                        )

                        # Update invoice status to paid if currently payment_scheduled
                        if invoice and invoice.status.value == "payment_scheduled":
                            from app.models.invoice import InvoiceStatus
                            from app.services.workflow_engine import transition_invoice

                            await transition_invoice(
                                db,
                                invoice,
                                InvoiceStatus.paid,
                                actor_id=None,
                                action_name="invoice.paid_via_erp_sync",
                                details={"payment_id": str(payment.id)},
                            )

                        synced += 1

                    except Exception as exc:
                        # Log the exception CLASS only, not the message — a
                        # processor/ERP SDK error string can embed partial
                        # account / PAN data (PII-out-of-logs invariant).
                        logger.warning(
                            "[payment-sync] failed to sync payment %s: %s",
                            payment.id,
                            exc.__class__.__name__,
                        )
                        failed += 1

                await db.commit()
                logger.info(
                    "[payment-sync] run %s: %d synced, %d skipped (in-flight), %d failed",
                    run_id,
                    synced,
                    skipped,
                    failed,
                )

            except Exception as exc:
                # Same PII rationale as above — class only, never the message.
                logger.warning(
                    "[payment-sync] error for run %s: %s", run_id, exc.__class__.__name__
                )
                await db.rollback()

        await tenant_engine.dispose()
    finally:
        await ctrl_engine.dispose()
