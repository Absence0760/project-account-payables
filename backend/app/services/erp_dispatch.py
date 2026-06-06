"""ERP dispatch — routes ERP sends to local in-process call or SQS/Lambda."""

import asyncio
import json
import threading
import uuid

import boto3

from app.config import settings


async def dispatch_erp(
    invoice_id: uuid.UUID,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Trigger ERP send via the configured mode (local or lambda)."""
    if settings.erp_mode == "lambda":
        _send_to_sqs(invoice_id, org_id, actor_id)
    else:
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
    """Put ERP job on SQS for Lambda to pick up."""
    client = boto3.client(
        "sqs",
        endpoint_url=settings.aws_endpoint_url or settings.s3_endpoint_url,  # LocalStack when set
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
    )
    client.send_message(
        QueueUrl=settings.sqs_erp_queue_url,
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
    """Run ERP send in-process with its own DB session and engine."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import _make_tenant_url
    from app.models.invoice import Invoice
    from app.models.organization import Organization
    from app.services.erp import send_to_erp_internal

    ctrl_engine = create_async_engine(settings.database_url)
    ctrl_factory = async_sessionmaker(ctrl_engine, expire_on_commit=False)

    try:
        async with ctrl_factory() as ctrl_db:
            result = await ctrl_db.execute(select(Organization).where(Organization.id == org_id))
            org = result.scalar_one_or_none()
            if not org:
                return

        erp_config = (org.settings or {}).get("erp")

        tenant_url = _make_tenant_url(org.db_name)
        tenant_engine = create_async_engine(tenant_url)
        tenant_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

        async with tenant_factory() as db:
            try:
                result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
                invoice = result.scalar_one_or_none()
                if not invoice:
                    return

                await send_to_erp_internal(db, invoice, actor_id=actor_id, erp_config=erp_config)
            except Exception:
                await db.rollback()

        await tenant_engine.dispose()
    finally:
        await ctrl_engine.dispose()
