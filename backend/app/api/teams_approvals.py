"""Microsoft Teams interactive approval — approve / reject from the Teams card.

``POST /api/approvals/teams/interactivity`` — PUBLIC, no JWT.

When an invoice is assigned for review and the org's chat provider is Teams, the
approval card posted to the channel carries Approve / Reject actions (rendered by
``chat_notification_adapters/teams_adapter``). Each action's payload carries a
signed, single-use **action token** — the same primitive the email-approval link
and the Slack buttons use (:mod:`app.services.email_action_token`), bound to the
``teams`` channel and to the intended approver. The token IS the credential;
there is no JWT, no session, so this endpoint is public-by-design and lives in
``NO_AUTH_REQUIRED``.

Two gates, layered, both fail closed (the exact same posture as the Slack path):

1. **Teams request signature** — a Teams Outgoing Webhook signs every POST as
   ``Authorization: HMAC <base64(hmac-sha256 over the raw body)>`` using a
   base64-encoded shared **security token** (``FEOH_TEAMS_SECURITY_TOKEN``). We
   base64-decode the secret, recompute the digest over the raw bytes, and compare
   constant-time. When Teams includes an ``X-Teams-Request-Timestamp`` header we
   also reject stale timestamps (> ``teams_request_max_age_seconds``) to stop
   replay of a captured POST. No secret configured → the feature is OFF and every
   request is rejected.
2. **Action token** — verified exactly like the email/Slack path (HMAC + expiry +
   ``teams`` channel + single-use ``jti`` consume in Redis), then the
   approve/reject runs through the *normal* :mod:`app.services.review` path as the
   named reviewer, so segregation of duties, the approval thresholds, the CFO
   gate, the immutable audit row, and the approval digital signature all apply
   exactly as if they had logged in.

Every rejection path returns an opaque ``200`` ack (a Teams-friendly message
Activity) — never a 4xx — so a probe can't enumerate tenants, invoices, or which
secret/token shapes are accepted. Best-effort: it never crashes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.email_actions import (
    _APPROVER_ROLES,
    _claim_jti,
    _load_reviewer,
    _release_jti,
    _resolve_org,
    _tenant_session,
)
from app.config import settings
from app.database import get_control_db
from app.models.invoice import InvoiceStatus
from app.services import review as review_svc
from app.services.email_action_token import (
    ACTION_APPROVE,
    CHANNEL_TEAMS,
    verify_action_token,
)

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/approvals/teams", tags=["teams-approval"])

# Hard cap on the interactivity body before buffering (memory-exhaustion guard on
# a public route). Teams Activity payloads are a few KB at most.
_MAX_BODY_BYTES = 256 * 1024


def _ack(message: str = "Thanks — your response was recorded.") -> JSONResponse:
    """An opaque, Teams-friendly message-Activity ack.

    The SAME shape is returned on success and on every rejection (bad signature,
    expired/replayed token, unknown invoice, feature off) so the response can't be
    used to enumerate. Teams renders the ``text`` back into the conversation.
    """
    return JSONResponse({"type": "message", "text": message})


def _verify_teams_signature(headers: dict, raw_body: bytes) -> bool:
    """Verify a Teams Outgoing Webhook HMAC and reject stale timestamps.

    Teams computes ``HMAC-SHA256(base64decode(security_token), raw_body)`` and
    sends it base64-encoded as ``Authorization: HMAC <base64-digest>``. We rebuild
    that and compare via constant-time ``hmac.compare_digest``. Fail closed: no
    secret, no/garbled header, a non-base64 secret, or a timestamp outside the
    replay window all return False. Never raises.
    """
    secret = settings.teams_security_token
    if not secret:
        return False
    lower = {k.lower(): v for k, v in headers.items()}

    # Optional replay guard — Teams does not always send a timestamp; only enforce
    # the window when the header is present (the jti + state machine still bound
    # replay either way).
    timestamp = lower.get("x-teams-request-timestamp")
    if timestamp is not None:
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            return False
        if abs(time.time() - ts) > settings.teams_request_max_age_seconds:
            return False

    auth = lower.get("authorization")
    if not auth or not auth.startswith("HMAC "):
        return False
    provided_b64 = auth[len("HMAC ") :].strip()
    try:
        key = base64.b64decode(secret)
        digest = hmac.new(key, raw_body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("ascii")
    except Exception:  # noqa: BLE001 — non-base64 secret / malformed input must fail closed
        return False
    return hmac.compare_digest(expected, provided_b64)


def _extract_token(payload: dict) -> str | None:
    """Pull the action token from a Teams Outgoing-Webhook Activity payload.

    The approval card's Action.Http button posts the token back on the Activity's
    ``value`` object (``value.token``). Some configurations instead echo it as the
    Activity ``text``; we accept either. Returns None on any shape mismatch (the
    verify step then rejects)."""
    value = payload.get("value")
    if isinstance(value, dict):
        token = value.get("token")
        if isinstance(token, str) and token:
            return token
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


@public_router.post("/interactivity")
async def teams_interactivity(
    request: Request,
    ctrl_db: AsyncSession = Depends(get_control_db),
) -> JSONResponse:
    """Handle a Teams Approve/Reject action.

    PUBLIC-BY-DESIGN, no JWT — the Teams HMAC + the action token are the gates.
    Returns an opaque 200 ack on every path (success AND rejection) so the
    response can't enumerate. ``get_control_db`` is a plain DB session, not an auth
    dependency (keeps the route in ``NO_AUTH_REQUIRED``); the tenant comes from the
    action token, never a header.
    """
    # Bound the body before buffering (public route).
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_BODY_BYTES:
                logger.warning("teams interactivity rejected: body exceeds size cap")
                return _ack()
        except ValueError:
            return _ack()

    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_BYTES:
        logger.warning("teams interactivity rejected: body exceeds size cap")
        return _ack()

    # 1. Teams request signature (constant-time HMAC + optional replay window).
    #    Fail closed when no secret is set (feature off).
    if not _verify_teams_signature(dict(request.headers), raw_body):
        logger.warning("teams interactivity signature rejected")
        return _ack()

    # 2. Parse the JSON Activity envelope.
    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            return _ack()
    except Exception:  # noqa: BLE001 — unparseable payload → opaque ack
        logger.warning("teams interactivity rejected: unparseable payload")
        return _ack()

    token = _extract_token(payload)

    # 3. Verify the action token (HMAC + expiry + teams-channel binding). The
    #    email/Slack link's signing key is reused; the `teams` channel keeps the
    #    surfaces' tokens non-interchangeable.
    decoded = verify_action_token(
        token, settings.email_action_signing_key, expected_channel=CHANNEL_TEAMS
    )
    if decoded is None:
        logger.warning("teams interactivity: invalid or expired action token")
        return _ack()

    org = await _resolve_org(ctrl_db, decoded.tenant_slug)
    if org is None:
        logger.warning("teams interactivity: unknown tenant")
        return _ack()

    reviewer = await _load_reviewer(ctrl_db, decoded.actor_id, org.id)
    if reviewer is None:
        logger.warning("teams interactivity: reviewer unavailable")
        return _ack()
    reviewer_roles = {r.name for r in (reviewer.roles or [])}
    if not (reviewer_roles & _APPROVER_ROLES):
        logger.warning("teams interactivity: reviewer not permitted")
        return _ack()

    # 4. Single-use consume on the token jti (closes the replay window — a
    #    re-clicked action can't double-act). Released below if the action turns
    #    out not to be applicable / permitted so the reviewer can still act in-app.
    claimed = await _claim_jti(decoded)
    if not claimed:
        return _ack("This approval has already been recorded.")

    try:
        async with _tenant_session(org) as db:
            committed, msg = await _apply_teams_action(db, decoded, reviewer, reviewer_roles)
            if committed:
                await db.commit()
            else:
                await db.rollback()
                await _release_jti(decoded)
            return _ack(msg)
    except Exception:  # noqa: BLE001 — never surface a stack trace on a public route
        # Threshold / CFO gate / segregation (HTTPException) and any other
        # failure: release the claim so the reviewer can sign in instead.
        logger.exception("teams interactivity: action failed for action=%s", decoded.action)
        await _release_jti(decoded)
        return _ack("Could not complete that action — please sign in to the app to review it.")


async def _apply_teams_action(db, decoded, reviewer, reviewer_roles) -> tuple[bool, str]:
    """Run approve/reject against a row-locked invoice. Returns (commit?, ack).

    Mirrors ``slack_approvals._apply_slack_action``. May raise HTTPException
    (threshold / segregation / CFO gate) — the caller releases the jti claim and
    acks generically."""
    from app.services.workflow_engine import get_invoice_for_update

    invoice = await get_invoice_for_update(db, decoded.invoice_id)
    if invoice.status != InvoiceStatus.ready_for_review:
        return False, "This invoice is no longer awaiting review."

    if decoded.action == ACTION_APPROVE:
        await review_svc.approve_invoice(
            db,
            invoice,
            actor_id=reviewer.id,
            actor_name=reviewer.full_name,
            actor_roles=reviewer_roles,
        )
        return True, f"Invoice {invoice.invoice_number} approved. Thank you."

    await review_svc.reject_invoice(
        db,
        invoice,
        actor_id=reviewer.id,
        actor_name=reviewer.full_name,
        reason="Rejected via Teams",
    )
    return True, f"Invoice {invoice.invoice_number} rejected."
