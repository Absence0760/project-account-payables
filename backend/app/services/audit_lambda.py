"""AWS Lambda handler for audit logging.

Triggered by SQS messages when audit_mode = "lambda".
Each message contains { correlation_id, organization_id, actor_id, action,
                        entity_type, entity_id, details, tenant_db_name }.

Deploy this module as the Lambda handler: app.services.audit_lambda.handler
"""

import asyncio
import json
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def handler(event, context):
    """AWS Lambda entry point — processes SQS batch."""
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        asyncio.get_event_loop().run_until_complete(_process_message(body))
    return {"statusCode": 200}


async def _process_message(body: dict) -> None:
    import os

    db_url = os.environ["DATABASE_URL"]
    tenant_db_name = body["tenant_db_name"]

    # Connect directly to the tenant DB
    tenant_url = db_url.rsplit("/", 1)[0] + "/" + tenant_db_name
    tenant_engine = create_async_engine(tenant_url)
    tenant_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

    async with tenant_factory() as db:
        try:
            from app.services.audit import log_action

            await log_action(
                db,
                correlation_id=uuid.UUID(body["correlation_id"]),
                organization_id=uuid.UUID(body["organization_id"]),
                actor_id=uuid.UUID(body["actor_id"]) if body.get("actor_id") else None,
                action=body["action"],
                entity_type=body["entity_type"],
                entity_id=uuid.UUID(body["entity_id"]),
                details=body.get("details"),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        finally:
            await tenant_engine.dispose()
