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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, get_org_id, require_roles
from app.config import settings
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


def _ack() -> JSONResponse:
    """Opaque 200 ack — the SAME body on every outcome once the request has
    passed signature verification: unknown/disabled intake token, no usable
    attachment, an internal processing failure, or genuine success.

    Anyone holding the platform-wide ``AP_EMAIL_INTAKE_SIGNING_SECRET`` (shared
    across all tenants, since the provider doesn't know tenants) could
    otherwise grind per-tenant intake tokens by watching for ``tenant_slug`` to
    populate in the response body. Per-request detail is logged server-side
    only. Mirrors ``slack_approvals.py``'s ``_ack`` helper.
    """
    return JSONResponse({"status": "received"})


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
    # Pre-signature rejections (unknown provider / bad signature / unparseable
    # body) return 204 silently so the response can't be used to enumerate
    # which providers / signing secrets / payload shapes the tenant accepts.
    # Distinct 4xx codes leaked that information. Once the signature verifies,
    # every remaining outcome — unknown/disabled intake token, duplicate
    # delivery, no usable attachments, a processing exception, or genuine
    # success — returns the SAME opaque 200 ack via `_ack()` below, so the
    # per-tenant intake token can't be enumerated either (see `_ack`'s
    # docstring).
    parser = get_parser(provider)
    if parser is None:
        logger.warning("Email intake: unknown provider %s", provider)
        return Response(status_code=204)

    # Bound the body BEFORE buffering it. A POST would otherwise be read fully
    # into memory before the signature check ever runs (memory-exhaustion DoS
    # on a public, unauthenticated route). Reject on the declared
    # Content-Length when present, and re-check the actual read in case the
    # header lied / was absent (chunked). Email payloads (incl. base64
    # attachments) can legitimately run larger than other webhooks; cap
    # defaults to a few MB.
    max_bytes = settings.email_intake_max_bytes
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                logger.warning("Email intake rejected: body exceeds size cap provider=%s", provider)
                return Response(status_code=204)
        except ValueError:
            logger.warning("Email intake rejected: invalid content-length provider=%s", provider)
            return Response(status_code=204)

    body = await request.body()
    if len(body) > max_bytes:
        logger.warning("Email intake rejected: body exceeds size cap provider=%s", provider)
        return Response(status_code=204)

    signature = (
        request.headers.get("X-Signature")
        or request.headers.get("X-Webhook-Signature")
        or request.headers.get("X-Mailgun-Signature-V2")
    )
    if not verify_signature(body, signature):
        logger.warning("Email intake signature rejected for provider=%s", provider)
        return Response(status_code=204)

    payload = parser(body, dict(request.headers))
    if payload is None:
        logger.warning("Email intake: could not parse payload for provider=%s", provider)
        return Response(status_code=204)

    try:
        result = await process_inbound_email(ctrl_db, payload)
    except Exception:  # noqa: BLE001 — never surface a stack trace / 500 on a public route
        logger.exception("Email intake: processing failed for provider=%s", provider)
        return _ack()

    # Log the real outcome server-side only; the response is a uniform ack
    # regardless of tenant/token resolution so it can't be used as an oracle.
    logger.info(
        "Email intake processed: provider=%s tenant=%s invoices_created=%d error=%s",
        provider,
        result.tenant_slug,
        len(result.invoices_created),
        result.error,
    )
    return _ack()


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
