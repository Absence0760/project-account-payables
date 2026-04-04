"""AWS Lambda handler for invoice extraction.

Triggered by SQS messages when extraction_mode = "lambda".
Each message contains { invoice_id, org_id, actor_id }.

Deploy this module as the Lambda handler: app.services.extraction_lambda.handler
"""

import asyncio
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def handler(event, context):
    """AWS Lambda entry point — processes SQS batch."""
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        asyncio.get_event_loop().run_until_complete(
            _process_message(body)
        )
    return {"statusCode": 200}


async def _process_message(body: dict) -> None:
    invoice_id = uuid.UUID(body["invoice_id"])
    org_id = uuid.UUID(body["org_id"])
    actor_id = uuid.UUID(body["actor_id"])

    # Build DB connections from environment (Lambda uses env vars, not app.config)
    import os

    db_url = os.environ["DATABASE_URL"]
    tenant_db_prefix = os.environ.get("TENANT_DB_PREFIX", "ap_")

    # Look up the org to find the tenant DB name
    control_engine = create_async_engine(db_url)
    control_factory = async_sessionmaker(control_engine, expire_on_commit=False)

    async with control_factory() as ctrl_db:
        from app.models.organization import Organization

        result = await ctrl_db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        if not org:
            await control_engine.dispose()
            return

    # Connect to the tenant DB
    tenant_url = db_url.rsplit("/", 1)[0] + "/" + org.db_name
    tenant_engine = create_async_engine(tenant_url)
    tenant_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

    async with tenant_factory() as db:
        try:
            from app.models.invoice import Invoice
            from app.services.extraction import run_extraction

            result = await db.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            )
            invoice = result.scalar_one_or_none()
            if not invoice:
                return

            await run_extraction(db, invoice, actor_id=actor_id)
        except Exception:
            await db.rollback()
            raise
        finally:
            await tenant_engine.dispose()
            await control_engine.dispose()
