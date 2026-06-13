"""PEPPOL AS4 inbound receive webhook (the receiver corner, C4).

``POST /api/peppol/inbound/{tenant_slug}`` — PUBLIC, no JWT.

Called by the receiver's Access Point (C3) when it delivers an inbound BIS
Billing 3.0 document to us. Security is the provider's HMAC signature over the
raw body (verified via :func:`peppol_receive.verify_inbound_signature` +
``webhook_security.extract_signature_header``). The tenant is encoded in the URL
path — each tenant configures its own inbound URL with the Access Point, so a
leaked URL only affects that one tenant (mirrors the payment webhook).

Every rejection path returns ``204`` silently with a PII-free ``logger.warning``
naming only a reason code — never a 4xx (which would enumerate which tenant
slugs / signing secrets / payload shapes are accepted) and never the supplier's
participant value / tax id / payload in the log or body.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_control_db
from app.models.organization import Organization
from app.services.peppol_adapters import get_peppol_adapter
from app.services.peppol_receive import receive_peppol_message, verify_inbound_signature
from app.services.webhook_security import extract_signature_header

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/peppol", tags=["peppol"])


@public_router.post("/inbound/{tenant_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def peppol_inbound_webhook(
    tenant_slug: str,
    request: Request,
    ctrl_db: AsyncSession = Depends(get_control_db),
) -> Response:
    """Receive an inbound PEPPOL document from the Access Point.

    PUBLIC-BY-DESIGN, no JWT — the HMAC signature is the gate. ``get_control_db``
    is a plain DB session, not an auth dependency (the route stays in
    ``NO_AUTH_REQUIRED``); the tenant is taken from the URL path, never a header.
    Returns 204 on every path — success AND every rejection — so the response
    can't be used to enumerate tenants or probe for the signing secret.
    """
    # 1. Master switch (mirrors email-intake's domain gate). Closed by default.
    if not settings.peppol_inbound_enabled:
        logger.warning("PEPPOL inbound disabled")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    body = await request.body()
    headers = dict(request.headers)

    # 2. Verify the gateway HMAC over the raw bytes. The signature is the gate
    #    on this public route (auth-before-everything for a public-by-design
    #    webhook). Reuse the multi-candidate header lookup from webhook_security.
    signature = extract_signature_header(
        headers, "X-Peppol-Signature", "X-Signature", "X-Webhook-Signature"
    )
    if not verify_inbound_signature(body, signature):
        logger.warning("PEPPOL inbound signature rejected")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 3. Resolve the tenant from the URL path. Never reveal which slugs exist.
    org = (
        await ctrl_db.execute(select(Organization).where(Organization.slug == tenant_slug))
    ).scalar_one_or_none()
    if org is None:
        logger.warning("PEPPOL inbound: unknown tenant")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 4. Parse the inbound metadata + payload via the tenant's configured
    #    adapter. None = unparseable / missing message id → can't dedupe →
    #    refuse (mirrors email-intake's parse-None drop).
    adapter = get_peppol_adapter((org.settings or {}).get("peppol"))
    message = adapter.parse_inbound(headers, body)
    if message is None or not message.message_id:
        logger.warning("PEPPOL inbound: unparseable delivery or missing message id")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 5. Hand off to the receive service. 204 regardless of the result —
    #    accepted, duplicate, and every soft-reject are all silent 204.
    result = await receive_peppol_message(ctrl_db, tenant_slug=org.slug, message=message)

    if not result.accepted:
        logger.warning("PEPPOL inbound not accepted: %s", result.reason or "unknown")

    return Response(status_code=status.HTTP_204_NO_CONTENT)
