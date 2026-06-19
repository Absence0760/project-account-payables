"""Signed, single-action tokens for email approval (approve/reject from email).

An AP reviewer who receives the "invoice assigned to you for review" email can
approve or reject the invoice straight from a link in that email — no login.
The link carries a **signed, expiring, single-action token** that IS the
credential: there is no JWT and no session. The token binds, under an
HMAC-SHA256 signature, the exact facts the action will run against:

    tenant_slug + invoice_id + actor_id (the reviewer) + action + expiry + jti

Because the platform holds the signing key (sops + KMS in deployed envs), the
token cannot be forged or tampered with — flipping the action, the invoice, or
the actor invalidates the signature. The action endpoint re-runs the *normal*
``services.review`` approve/reject path as that reviewer, so segregation of
duties, approval thresholds, the CFO gate, the immutable audit row, and the
approval digital-signature all still apply exactly as if they had logged in.

This module is **pure**: no DB, no network, no settings import. The signing key
and TTL are passed in by the caller. An empty key fails closed — ``build_*``
returns ``None`` (no link is added to the email) and ``verify_action_token``
returns ``None`` (every token is rejected) — mirroring the fail-closed posture
of :mod:`app.services.approval_signature` and
:func:`app.services.webhook_security.verify_hmac_sha256`. There is NO hardcoded
production fallback key.

Single-use is enforced two ways, layered:
  1. the workflow state machine — approve/reject move the invoice out of
     ``ready_for_review``, so a replay can't re-fire the same decision; and
  2. a Redis consume on the ``jti`` at the endpoint — which also closes the
     resubmit-replay window (a stale token reused after a reject→resubmit cycle).
This module only owns the token's integrity + expiry; the endpoint owns consume.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from urllib.parse import quote

# The two actions a token may authorize. Anything else is rejected on verify.
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
_VALID_ACTIONS = frozenset({ACTION_APPROVE, ACTION_REJECT})


@dataclass(frozen=True)
class ActionToken:
    """The verified, still-valid facts decoded from a token."""

    tenant_slug: str
    invoice_id: uuid.UUID
    actor_id: uuid.UUID
    action: str
    jti: str
    exp: int


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(body: str) -> bytes:
    # Restore stripped padding before decoding.
    padding = "=" * (-len(body) % 4)
    return base64.urlsafe_b64decode(body + padding)


def _sign(body: str, signing_key: str) -> str:
    return hmac.new(signing_key.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()


def build_action_token(
    *,
    tenant_slug: str,
    invoice_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    signing_key: str,
    ttl_hours: int,
    now: float | None = None,
) -> str | None:
    """Build a ``<b64url-payload>.<hex-hmac>`` token, or ``None`` if disabled.

    Returns ``None`` when no signing key is configured (feature off) or the
    action is not one of the two valid actions — so a caller can simply skip
    adding the link. The signature covers the base64 payload string, so the
    exact transmitted bytes are what gets authenticated.
    """
    if not signing_key or action not in _VALID_ACTIONS:
        return None
    issued = now if now is not None else time.time()
    payload = {
        "t": tenant_slug,
        "i": str(invoice_id),
        "a": str(actor_id),
        "act": action,
        "exp": int(issued) + ttl_hours * 3600,
        "jti": secrets.token_urlsafe(9),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body = _b64url_encode(raw)
    return f"{body}.{_sign(body, signing_key)}"


def verify_action_token(
    token: str | None,
    signing_key: str,
    *,
    now: float | None = None,
) -> ActionToken | None:
    """Verify signature + expiry and return the decoded facts, or ``None``.

    Returns ``None`` — never raises — on an empty key, a malformed token, a bad
    signature, an unknown action, a malformed payload, or an expired token, so
    every rejection path surfaces as a friendly "invalid/expired link" rather
    than a 500. Constant-time signature comparison via ``hmac.compare_digest``.
    """
    if not signing_key or not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    if not body or not sig:
        return None
    expected = _sign(body, signing_key)
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        data = json.loads(_b64url_decode(body))
        action = data["act"]
        if action not in _VALID_ACTIONS:
            return None
        exp = int(data["exp"])
        decoded = ActionToken(
            tenant_slug=str(data["t"]),
            invoice_id=uuid.UUID(str(data["i"])),
            actor_id=uuid.UUID(str(data["a"])),
            action=action,
            jti=str(data["jti"]),
            exp=exp,
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
    current = now if now is not None else time.time()
    if exp < current:
        return None
    return decoded


def build_email_action_links(
    *,
    api_base_url: str,
    tenant_slug: str,
    invoice_id: uuid.UUID,
    actor_id: uuid.UUID,
    signing_key: str,
    ttl_hours: int,
    now: float | None = None,
) -> tuple[str, str] | None:
    """Build the (plaintext, html) Approve/Reject link block for an email.

    Returns ``None`` when the feature is disabled (no key) so the caller adds
    nothing to the email. The links land on the *confirmation* GET endpoint —
    which only renders a page; the state change happens on the POST the user
    submits from there — so email link-prefetchers / security scanners that
    issue a bare GET can never auto-approve an invoice.
    """
    approve = build_action_token(
        tenant_slug=tenant_slug,
        invoice_id=invoice_id,
        actor_id=actor_id,
        action=ACTION_APPROVE,
        signing_key=signing_key,
        ttl_hours=ttl_hours,
        now=now,
    )
    reject = build_action_token(
        tenant_slug=tenant_slug,
        invoice_id=invoice_id,
        actor_id=actor_id,
        action=ACTION_REJECT,
        signing_key=signing_key,
        ttl_hours=ttl_hours,
        now=now,
    )
    if not approve or not reject:
        return None

    base = api_base_url.rstrip("/")
    approve_url = f"{base}/api/invoices/email-action/{quote(approve, safe='')}"
    reject_url = f"{base}/api/invoices/email-action/{quote(reject, safe='')}"

    text = f"Approve: {approve_url}\nReject:  {reject_url}"
    html = (
        '<p style="margin-top:16px">'
        f'<a href="{approve_url}" '
        'style="display:inline-block;padding:8px 16px;margin-right:8px;'
        'background:#16a34a;color:#fff;text-decoration:none;border-radius:4px">'
        "Approve</a>"
        f'<a href="{reject_url}" '
        'style="display:inline-block;padding:8px 16px;'
        'background:#dc2626;color:#fff;text-decoration:none;border-radius:4px">'
        "Reject</a>"
        "</p>"
    )
    return text, html
