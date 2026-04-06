"""Extraction dispatch — routes extraction to local background task or SQS/Lambda."""

import asyncio
import json
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
        asyncio.create_task(
            _run_local(invoice_id, org_id, actor_id)
        )


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
        MessageBody=json.dumps({
            "invoice_id": str(invoice_id),
            "org_id": str(org_id),
            "actor_id": str(actor_id),
        }),
        MessageGroupId=str(invoice_id),
    )


async def _run_local(
    invoice_id: uuid.UUID,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Run extraction in-process with its own DB session."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database import control_session_factory, get_tenant_engine
    from app.models.invoice import Invoice
    from app.models.organization import Organization
    from app.services.extraction import run_extraction

    # Look up the tenant DB name
    async with control_session_factory() as ctrl_db:
        result = await ctrl_db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        if not org:
            return

    engine = get_tenant_engine(org.db_name)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        try:
            result = await db.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            )
            invoice = result.scalar_one_or_none()
            if not invoice:
                return

            await run_extraction(db, invoice, actor_id=actor_id, org_settings=org.settings)
        except Exception:
            await db.rollback()
