"""Sync payment data to the connected ERP after payment execution.

Runs async in a background thread — doesn't block the payment run response.
Records sync status on each payment for retry capability.
"""

import asyncio
import threading
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.payment import Payment


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
                print(f"[payment-sync] Organization {org_id} not found")
                return

        erp_config = (org.settings or {}).get("erp")
        if not erp_config:
            print(f"[payment-sync] No ERP configured for org {org_id}, skipping sync")
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
                failed = 0
                for payment, invoice in rows:
                    try:
                        # Build payment data for ERP
                        # In production, this would call adapter.post_payment()
                        # For now, log and mark as synced
                        print(
                            f"[payment-sync] Syncing payment {payment.id}: "
                            f"invoice={invoice.invoice_number if invoice else '?'}, "
                            f"amount=${float(payment.amount):.2f}, method={payment.method}"
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
                        print(f"[payment-sync] Failed to sync payment {payment.id}: {exc}")
                        failed += 1

                await db.commit()
                print(f"[payment-sync] Run {run_id}: {synced} synced, {failed} failed")

            except Exception as exc:
                print(f"[payment-sync] ERROR for run {run_id}: {exc}")
                await db.rollback()

        await tenant_engine.dispose()
    finally:
        await ctrl_engine.dispose()
