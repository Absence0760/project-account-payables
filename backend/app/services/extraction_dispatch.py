"""Extraction dispatch — routes extraction to local background task or SQS/Lambda."""

import asyncio
import json
import threading
import uuid

import boto3

from app.config import settings


async def dispatch_extraction(
    invoice_id: uuid.UUID,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Trigger extraction via the configured mode (local or lambda)."""
    if settings.extraction_mode == "lambda":
        _send_to_sqs(invoice_id, org_id, actor_id)
    else:
        # Run in a new thread with its own event loop to avoid greenlet conflicts
        # with SQLAlchemy async sessions
        thread = threading.Thread(
            target=_run_in_thread,
            args=(invoice_id, org_id, actor_id),
            daemon=True,
        )
        thread.start()


def _run_in_thread(
    invoice_id: uuid.UUID,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Create a fresh event loop in a background thread and run extraction."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_local(invoice_id, org_id, actor_id))
    finally:
        loop.close()


def _send_to_sqs(
    invoice_id: uuid.UUID,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Put extraction job on SQS for Lambda to pick up."""
    client = boto3.client(
        "sqs",
        endpoint_url=settings.s3_endpoint_url,  # reuse endpoint for LocalStack
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

    # Create a fresh control-plane engine for this thread
    ctrl_engine = create_async_engine(settings.database_url)
    ctrl_factory = async_sessionmaker(ctrl_engine, expire_on_commit=False)

    try:
        # Look up the tenant DB name
        async with ctrl_factory() as ctrl_db:
            result = await ctrl_db.execute(select(Organization).where(Organization.id == org_id))
            org = result.scalar_one_or_none()
            if not org:
                print(f"[extraction] Organization {org_id} not found")
                return

        # Create a fresh tenant engine for this thread
        tenant_url = _make_tenant_url(org.db_name)
        tenant_engine = create_async_engine(tenant_url)
        tenant_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

        async with tenant_factory() as db:
            try:
                result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
                invoice = result.scalar_one_or_none()
                if not invoice:
                    print(f"[extraction] Invoice {invoice_id} not found")
                    return

                print(
                    f"[extraction] Starting extraction for "
                    f"invoice {invoice_id}, "
                    f"file_key={invoice.file_key}"
                )
                await run_extraction(db, invoice, actor_id=actor_id, org_settings=org.settings)
                print(
                    f"[extraction] Completed extraction for "
                    f"invoice {invoice_id}, "
                    f"status={invoice.status}"
                )
            except Exception as exc:
                print(f"[extraction] ERROR for invoice {invoice_id}: {exc}")
                import traceback

                traceback.print_exc()
                await db.rollback()

        await tenant_engine.dispose()
    finally:
        await ctrl_engine.dispose()
