"""Inbound billing-provider webhook (Stripe Billing et al.).

``POST /api/billing/webhook/{provider}`` — PUBLIC, no JWT.

Called by the billing provider (Stripe) when a subscription's lifecycle changes
(trial ends, payment fails → ``past_due``, dunning exhausts → ``canceled``).
Billing is **control-plane** (keyed by org), so — unlike the payment webhook,
which carries the tenant slug in its path — this route resolves the affected
``Subscription`` by the provider's subscription id carried *in the event itself*
(``external_subscription_id``). The provider id is the tenant boundary here.

Security mirrors the other inbound webhooks (project invariant #9):
  1. master switch + body-size cap (memory-exhaustion guard on a public route);
  2. provider HMAC verification over the raw bytes — done inside the adapter's
     ``parse_webhook`` (fail-closed: no secret / bad signature → ``None``);
  3. dedupe by ``event_id`` via ``webhook_security.is_event_already_processed``
     (a provider retries on any non-2xx; dedup keeps a one-time effect one-time);
  4. drive the idempotent ``Subscription`` status transition + append-only audit.

Every rejection path — disabled switch, oversized body, bad signature, unknown
provider, missing/duplicate event id, unknown subscription — returns **204
silently** with a PII-free ``logger.warning`` naming only a reason code. A
distinct 4xx would enumerate which providers / secrets / subscription ids are
accepted.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_control_db
from app.services.billing.webhook_processing import apply_billing_event
from app.services.billing_adapters import get_billing_adapter
from app.services.webhook_security import (
    is_event_already_processed,
    release_event_claim,
)

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/billing", tags=["billing"])

# Billing webhook bodies are small JSON envelopes (a few KB). Cap well below the
# PEPPOL/punch-out document cap — a signed-but-oversized POST is rejected before
# it is buffered/parsed.
_MAX_WEBHOOK_BYTES = 512 * 1024


@public_router.post("/webhook/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def billing_webhook(
    provider: str,
    request: Request,
    control_db: AsyncSession = Depends(get_control_db),
) -> Response:
    """Receive a billing-provider lifecycle webhook.

    PUBLIC-BY-DESIGN, no JWT — the provider HMAC is the gate. ``get_control_db``
    is a plain DB session, not an auth dependency (the route stays in
    ``NO_AUTH_REQUIRED``). Returns 204 on every path so the response can't be
    used to enumerate providers, secrets, or subscription ids.
    """
    # 1. Master switch — closed by default in local dev (no outbound billing
    #    integration), flipped on in deployed envs alongside the live provider.
    if not settings.billing_webhook_enabled:
        logger.warning("billing webhook disabled")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 1b. Bound the body BEFORE buffering. Reject on a too-large declared
    #     Content-Length, and re-check the actual read (header may lie / be
    #     absent on a chunked request).
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_WEBHOOK_BYTES:
                logger.warning("billing webhook rejected: body exceeds size cap")
                return Response(status_code=status.HTTP_204_NO_CONTENT)
        except ValueError:
            logger.warning("billing webhook rejected: invalid content-length")
            return Response(status_code=status.HTTP_204_NO_CONTENT)

    body = await request.body()
    if len(body) > _MAX_WEBHOOK_BYTES:
        logger.warning("billing webhook rejected: body exceeds size cap")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    headers = dict(request.headers)

    # 2. Build the named adapter (the dispatcher injects the process-level keys
    #    from config; the per-org override doesn't apply here — we haven't
    #    resolved the org yet, the event carries the provider subscription id).
    #    An unknown provider name falls back to `mock`, whose `parse_webhook`
    #    expects a dev JSON envelope; a real Stripe POST to a `mock`-configured
    #    deployment simply fails to parse → 204. Verify the name matches the
    #    configured provider so we don't quietly accept a different one.
    if provider != settings.billing_provider:
        logger.warning("billing webhook rejected: provider mismatch")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    adapter = get_billing_adapter(provider)

    # 3. Verify the HMAC + normalize (inside the adapter). None = bad signature /
    #    unparseable / missing id|type → silent 204 (no enumeration).
    event = adapter.parse_webhook(headers, body)
    if event is None:
        logger.warning("billing webhook signature/parse rejected")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 4. Dedupe by event id (Redis SET NX EX). A provider redelivery within the
    #    window short-circuits — the lifecycle effect already ran exactly once.
    #    NB: the claim key MUST match the release-on-failure key below exactly,
    #    or the release is a no-op against a different Redis key.
    dedup_provider = f"billing:{provider}"
    if await is_event_already_processed(dedup_provider, event.event_id):
        logger.info("billing webhook duplicate event ignored")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 5. Apply the (idempotent) subscription transition + append-only audit.
    #    The dedup claim (step 4) is durable the instant it's written, but the
    #    lifecycle effect it guards isn't durable until `apply_billing_event`
    #    commits. If that commit — or anything before it — raises, release the
    #    claim so the provider's retry can reprocess; otherwise the transition
    #    (e.g. → past_due) is silently deduped away for the full TTL and lost.
    #    Mirrors the claim/release discipline in `api/cards.py::card_webhook`.
    #    We re-raise (→ 5xx) so the provider retries, matching the prior
    #    contract (no try/except → the exception propagated to a 500 already).
    try:
        result = await apply_billing_event(control_db, event=event)
    except Exception:
        await release_event_claim(dedup_provider, event.event_id)
        raise
    if not result.applied:
        logger.warning("billing webhook not applied: %s", result.reason or "unknown")

    return Response(status_code=status.HTTP_204_NO_CONTENT)
