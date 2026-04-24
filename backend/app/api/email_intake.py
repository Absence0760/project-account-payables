"""Email-intake webhooks + org-admin token management.

Two classes of endpoint:

1. ``POST /api/email-intake/inbound/{provider}`` — PUBLIC, no JWT.
   Called by the email provider (SES via SNS, Mailgun, etc). Security
   is HMAC-signature verification of the body, plus the opaque
   per-tenant token embedded in the recipient address. Both layers are
   required in prod — see backend/docs/email-intake.md.

2. ``/api/organization/email-intake/*`` — JWT-authenticated, admin only.
   Shows the current intake address and rotates the token on demand
   (e.g. after a suspected leak).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, get_org_id, require_roles
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.services.email_intake import (
    intake_address_for,
    process_inbound_email,
    provision_intake_token,
    verify_signature,
)
from app.services.email_intake_adapters import get_parser

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/email-intake", tags=["email-intake"])
admin_router = APIRouter(prefix="/organization/email-intake", tags=["email-intake"])


# ---------------------------------------------------------------------------
# Public webhook
# ---------------------------------------------------------------------------


@public_router.post("/inbound/{provider}", status_code=status.HTTP_200_OK)
async def inbound_webhook(
    provider: str,
    request: Request,
    ctrl_db: AsyncSession = Depends(get_control_db),
):
    """Webhook endpoint the email provider posts to on each inbound message.

    Signature header names differ per provider; we check a few common ones
    so founders can point any provider at this URL without rewriting it.
    """
    parser = get_parser(provider)
    if parser is None:
        raise HTTPException(status_code=404, detail=f"Unknown email provider: {provider}")

    body = await request.body()
    signature = (
        request.headers.get("X-Signature")
        or request.headers.get("X-Webhook-Signature")
        or request.headers.get("X-Mailgun-Signature-V2")
    )
    if not verify_signature(body, signature):
        logger.warning("Email intake signature rejected for provider=%s", provider)
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = parser(body, dict(request.headers))
    if payload is None:
        raise HTTPException(status_code=400, detail="Could not parse provider payload")

    result = await process_inbound_email(ctrl_db, payload)
    return result.to_dict()


# ---------------------------------------------------------------------------
# Admin: show / rotate intake address
# ---------------------------------------------------------------------------


@admin_router.get("", status_code=status.HTTP_200_OK)
async def get_intake_address(
    ctrl_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    org = await _get_org_or_404(ctrl_db, org_id)
    return {
        "address": intake_address_for(org),
        "enabled": bool(((org.settings or {}).get("email_intake") or {}).get("enabled")),
    }


@admin_router.post("/rotate-token", status_code=status.HTTP_200_OK)
async def rotate_intake_token(
    ctrl_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Generate a new token; the old address stops accepting email immediately."""
    org = await _get_org_or_404(ctrl_db, org_id)
    provision_intake_token(org)
    await ctrl_db.commit()
    await ctrl_db.refresh(org)
    return {"address": intake_address_for(org)}


async def _get_org_or_404(ctrl_db: AsyncSession, org_id: uuid.UUID) -> Organization:
    q = await ctrl_db.execute(select(Organization).where(Organization.id == org_id))
    org = q.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org
