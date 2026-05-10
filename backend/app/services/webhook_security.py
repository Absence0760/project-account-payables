"""Shared webhook-security primitives.

Project invariant #9 says every inbound webhook must
  - verify the provider's HMAC over the raw bytes, and
  - dedupe by event id so a re-delivery doesn't replay a one-time
    effect (a settled payment, a card rebate, an ERP status flip).

The payment adapter implements its own HMAC verification inline. This
module exposes the same primitives in a form the card- and ERP-webhook
handlers (which don't go through the adapter abstraction) can reuse.

Dedup uses Redis with a TTL so retries within the provider's
re-delivery window are caught, but the key set doesn't grow without
bound. The 24-hour default covers every processor we integrate with;
most retry for a few hours then back off.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from app.redis import get_redis

logger = logging.getLogger(__name__)

DEDUP_PREFIX = "webhook:event:"
DEFAULT_DEDUP_TTL_SECONDS = 24 * 60 * 60


def verify_hmac_sha256(secret: str, raw_body: bytes, provided_hex: str | None) -> bool:
    """Constant-time HMAC-SHA256 check.

    Returns False (not raises) so the caller can decide whether to
    log the rejection silently or return an error. Empty / missing
    secret or missing signature always fail — there's no
    "no secret = anyone can post" mode.
    """
    if not secret or not provided_hex:
        return False
    try:
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    except Exception:  # noqa: BLE001 — defensive: malformed input must fail closed
        return False
    return hmac.compare_digest(expected, provided_hex)


async def is_event_already_processed(
    provider: str, event_id: str, *, ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS
) -> bool:
    """Record an event id (provider + event) and return True if it
    was already recorded within the TTL window.

    The set-if-not-exists semantics mean callers can do::

        if await is_event_already_processed("lithic", evt.id):
            return  # silent ack, same response as first delivery

    A `False` return means the caller is the *first* to claim this
    event id — they must drive the side effect now.
    """
    if not event_id:
        # Empty / missing event id can't be deduped — treat as "always
        # first delivery" but log so an operator can spot a provider
        # that's stopped including event ids.
        logger.info("[webhook-dedup] missing event id for provider=%s", provider)
        return False
    r = await get_redis()
    key = f"{DEDUP_PREFIX}{provider}:{event_id}"
    # SET key value NX EX <ttl> — returns OK if set, nil if key already
    # existed. redis-py's `set` returns True / None for that.
    was_set = await r.set(key, "1", nx=True, ex=ttl_seconds)
    return not was_set


def extract_signature_header(headers: dict, *candidates: str) -> str | None:
    """Pull the first present signature header out of a request.

    Different providers use different header names (Lithic:
    `Webhook-Signature`, Stripe-style: `Stripe-Signature`,
    generic: `X-Signature`). Headers are case-insensitive in HTTP,
    so we walk the dict comparing lower-cased names.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None
