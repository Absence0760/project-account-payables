"""Outbound-webhook management — subscription CRUD + delivery log + redelivery.

Admin-facing endpoints, gated by the normal JWT session + ``require_roles(
ROLE_ADMIN)`` (NOT the X-API-Key path — outbound-webhook config is a
control-plane admin action). They operate on the control-plane
``webhook_subscriptions`` / ``webhook_deliveries`` tables.

Create returns the signing secret EXACTLY ONCE (like an API-key mint); list/get
responses carry only ``secret_prefix`` — never the full secret. Every mutation
writes an append-only, PII-free audit row.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, require_roles
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.models.webhook import (
    DELIVERY_DEAD,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    WEBHOOK_EVENT_TYPES,
    WebhookDelivery,
    WebhookSubscription,
)
from app.services.audit_dispatch import dispatch_auth_audit
from app.services.webhooks.signing import generate_signing_secret
from app.services.webhooks.url_guard import (
    REJECT_DETAIL,
    WebhookTargetNotAllowed,
    ensure_public_webhook_target,
)
from app.tenant import get_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _require_public_target(target_url: str) -> None:
    """SSRF gate on the supplied target URL (resolves the host; rejects
    loopback / private / link-local / metadata / other non-public addresses).
    One generic, non-enumerating 422 for every rejection reason. The same
    guard runs again immediately before each dispatch (DNS-rebinding TOCTOU)."""
    try:
        await ensure_public_webhook_target(target_url)
    except WebhookTargetNotAllowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=REJECT_DETAIL
        ) from None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateSubscriptionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    target_url: str = Field(..., min_length=1, max_length=2048)
    event_types: list[str] = Field(..., min_length=1)

    @field_validator("target_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("target_url must be an http(s) URL")
        return v

    @field_validator("event_types")
    @classmethod
    def _validate_events(cls, v: list[str]) -> list[str]:
        unknown = [e for e in v if e not in WEBHOOK_EVENT_TYPES]
        if unknown:
            raise ValueError(
                f"unknown event type(s): {', '.join(unknown)}. "
                f"Allowed: {', '.join(WEBHOOK_EVENT_TYPES)}"
            )
        # de-dupe, preserve order
        return list(dict.fromkeys(v))


class UpdateSubscriptionRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    target_url: str | None = Field(None, min_length=1, max_length=2048)
    event_types: list[str] | None = None
    active: bool | None = None

    @field_validator("target_url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("target_url must be an http(s) URL")
        return v

    @field_validator("event_types")
    @classmethod
    def _validate_events(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("event_types cannot be empty")
        unknown = [e for e in v if e not in WEBHOOK_EVENT_TYPES]
        if unknown:
            raise ValueError(f"unknown event type(s): {', '.join(unknown)}")
        return list(dict.fromkeys(v))


class SubscriptionResponse(BaseModel):
    """Subscription metadata — never carries the full signing secret."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    target_url: str
    event_types: list[str]
    secret_prefix: str
    active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SubscriptionCreatedResponse(BaseModel):
    """The create response — the ONLY place the signing secret is returned."""

    subscription: SubscriptionResponse
    signing_secret: str


class DeliveryResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    subscription_id: uuid.UUID
    event_id: str
    event_type: str
    status: str
    attempt_count: int
    response_code: int | None = None
    next_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=SubscriptionCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: CreateSubscriptionRequest,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> SubscriptionCreatedResponse:
    """Create a webhook subscription. Returns the signing secret ONCE."""
    await _require_public_target(body.target_url)
    secret, prefix = generate_signing_secret()
    row = WebhookSubscription(
        id=uuid.uuid4(),
        organization_id=org.id,
        name=body.name,
        target_url=body.target_url,
        event_types=body.event_types,
        signing_secret=secret,
        secret_prefix=prefix,
        active=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await dispatch_auth_audit(
        organization_id=org.id,
        actor_id=user.id,
        action="webhook_subscription.created",
        entity_id=row.id,
        details={
            "name": row.name,
            "event_types": row.event_types,
            "secret_prefix": row.secret_prefix,
        },
    )
    return SubscriptionCreatedResponse(
        subscription=SubscriptionResponse.model_validate(row),
        signing_secret=secret,
    )


@router.get("", response_model=list[SubscriptionResponse])
async def list_subscriptions(
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> list[SubscriptionResponse]:
    rows = (
        (
            await db.execute(
                select(WebhookSubscription)
                .where(WebhookSubscription.organization_id == org.id)
                .order_by(WebhookSubscription.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [SubscriptionResponse.model_validate(r) for r in rows]


async def _get_owned_subscription(
    db: AsyncSession, sub_id: uuid.UUID, org_id: uuid.UUID
) -> WebhookSubscription:
    row = (
        await db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.id == sub_id,
                WebhookSubscription.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # Same 404 for wrong-org and missing so we don't enumerate another
        # tenant's subscription ids.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook subscription not found"
        )
    return row


@router.patch("/{sub_id}", response_model=SubscriptionResponse)
async def update_subscription(
    sub_id: uuid.UUID,
    body: UpdateSubscriptionRequest,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> SubscriptionResponse:
    if body.target_url is not None:
        await _require_public_target(body.target_url)
    row = await _get_owned_subscription(db, sub_id, org.id)
    changed: dict = {}
    if body.name is not None:
        row.name = body.name
        changed["name"] = body.name
    if body.target_url is not None:
        row.target_url = body.target_url
        changed["target_url_changed"] = True  # never log the URL value (may carry a token)
    if body.event_types is not None:
        row.event_types = body.event_types
        changed["event_types"] = body.event_types
    if body.active is not None:
        row.active = body.active
        changed["active"] = body.active
    if changed:
        await db.commit()
        await db.refresh(row)
        await dispatch_auth_audit(
            organization_id=org.id,
            actor_id=user.id,
            action="webhook_subscription.updated",
            entity_id=row.id,
            details=changed,
        )
    return SubscriptionResponse.model_validate(row)


@router.delete("/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    sub_id: uuid.UUID,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> None:
    row = await _get_owned_subscription(db, sub_id, org.id)
    await db.delete(row)  # CASCADE removes its deliveries
    await db.commit()
    await dispatch_auth_audit(
        organization_id=org.id,
        actor_id=user.id,
        action="webhook_subscription.deleted",
        entity_id=sub_id,
        details={"name": row.name},
    )


# ---------------------------------------------------------------------------
# Delivery log + redelivery
# ---------------------------------------------------------------------------


@router.get("/deliveries", response_model=list[DeliveryResponse])
async def list_deliveries(
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
    subscription_id: uuid.UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> list[DeliveryResponse]:
    """List this org's webhook deliveries (newest first), optionally filtered."""
    stmt = select(WebhookDelivery).where(WebhookDelivery.organization_id == org.id)
    if subscription_id is not None:
        stmt = stmt.where(WebhookDelivery.subscription_id == subscription_id)
    if status_filter is not None:
        stmt = stmt.where(WebhookDelivery.status == status_filter)
    stmt = (
        stmt.order_by(WebhookDelivery.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [DeliveryResponse.model_validate(r) for r in rows]


@router.post("/deliveries/{delivery_id}/redeliver", response_model=DeliveryResponse)
async def redeliver(
    delivery_id: uuid.UUID,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> DeliveryResponse:
    """Re-enqueue a failed / dead delivery and attempt it immediately.

    Only ``failed`` / ``dead`` deliveries can be redelivered — a re-send of an
    already-``delivered`` row would double-fire a side effect at the receiver.
    Resets the attempt counter and re-processes inline so the response reflects
    the new outcome.
    """
    row = (
        await db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.id == delivery_id,
                WebhookDelivery.organization_id == org.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook delivery not found"
        )
    if row.status not in (DELIVERY_FAILED, DELIVERY_DEAD):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed or dead deliveries can be redelivered.",
        )

    row.status = DELIVERY_PENDING
    row.attempt_count = 0
    row.next_attempt_at = datetime.now(UTC)
    await db.commit()

    await dispatch_auth_audit(
        organization_id=org.id,
        actor_id=user.id,
        action="webhook_delivery.redelivered",
        entity_id=row.id,
        details={"event_type": row.event_type, "event_id": row.event_id},
    )

    # Attempt inline so the caller sees the result. Best-effort — the row's
    # outcome is persisted regardless; the sweep retries if this fails.
    from app.services.webhooks.delivery import process_delivery

    await process_delivery(db, row)
    await db.refresh(row)
    return DeliveryResponse.model_validate(row)
