"""Audit dispatch — routes audit log writes to local in-process or SQS/Lambda."""

import json
import uuid

import boto3
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


async def dispatch_audit(
    db: AsyncSession,
    *,
    correlation_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    details: dict | None = None,
):
    """Write audit log locally or dispatch to SQS for Lambda processing."""
    if settings.audit_mode == "lambda":
        tenant_db_name = await _resolve_tenant_db_name(organization_id)
        _send_to_sqs(
            tenant_db_name=tenant_db_name,
            correlation_id=correlation_id,
            organization_id=organization_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
    else:
        from app.services.audit import log_action

        await log_action(
            db,
            correlation_id=correlation_id,
            organization_id=organization_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )


async def _resolve_tenant_db_name(organization_id: uuid.UUID) -> str:
    """Look up the tenant DB name from the control plane."""
    from app.database import control_session_factory
    from app.models.organization import Organization

    async with control_session_factory() as ctrl_db:
        result = await ctrl_db.execute(
            select(Organization.db_name).where(Organization.id == organization_id)
        )
        db_name = result.scalar_one_or_none()
        if not db_name:
            raise ValueError(f"Organization {organization_id} not found")
        return db_name


def _send_to_sqs(
    *,
    tenant_db_name: str,
    correlation_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    details: dict | None,
) -> None:
    """Put audit event on SQS for Lambda to pick up."""
    client = boto3.client(
        "sqs",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
    )
    client.send_message(
        QueueUrl=settings.sqs_audit_queue_url,
        MessageBody=json.dumps({
            "tenant_db_name": tenant_db_name,
            "correlation_id": str(correlation_id),
            "organization_id": str(organization_id),
            "actor_id": str(actor_id) if actor_id else None,
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "details": details,
        }),
        MessageGroupId=str(correlation_id),
    )
