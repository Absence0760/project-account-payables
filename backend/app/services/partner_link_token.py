"""Signed, single-use partner *link codes* (two-sided consent for attach).

A partner (reseller) org administers branded CHILD tenants, linked by the
control-plane self-FK ``Organization.parent_org_id``. The administration surface
(``/api/partner``) manages *existing* links; this module is the credential that
lets a partner *create* one **without** a privilege-escalation hole.

The hard question attach poses: who may declare org X a child of partner P?
Letting any partner admin unilaterally adopt an arbitrary org would be a
cross-tenant takeover. The answer here is **two-sided consent**:

  1. The *prospective child's own admin* mints a short-lived link code
     (``POST /api/partner/link-code``) — proof the child CONSENTS to being
     adopted. The code binds, under an HMAC-SHA256 signature, exactly:

         child_org_id + expiry + jti

  2. The *partner's admin* redeems that code (``POST /api/partner/children``)
     to attach. The signature proves the platform minted it for that child, so
     a partner can't forge one or point it at an org that didn't consent.

This mirrors the ``email_action_token`` design: a pure, stateless HMAC token
(no DB, no network, no settings import here — the key + TTL are passed in by the
caller). An empty key fails closed (``build_*``/``verify_*`` return ``None``),
so the feature is OFF until a key is configured — exactly like the other HMAC
secrets, with NO hardcoded production fallback. Single-use is enforced by the
caller via a Redis consume on the ``jti`` (the code can't be replayed to adopt a
detached-then-reattached org with a stale code).

The token carries ONLY the child org id — never a name, slug, or any PII — so a
code that leaks reveals nothing about the tenant beyond an opaque UUID it was
already issued for.
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

# Bind a link code to its single purpose so this token can't be confused with
# (or replayed as) any other HMAC token the platform mints.
_PURPOSE = "partner_link"


@dataclass(frozen=True)
class PartnerLinkToken:
    """The verified, still-valid facts decoded from a link code."""

    child_org_id: uuid.UUID
    jti: str
    exp: int


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(body: str) -> bytes:
    padding = "=" * (-len(body) % 4)
    return base64.urlsafe_b64decode(body + padding)


def _sign(body: str, signing_key: str) -> str:
    return hmac.new(signing_key.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()


def build_link_code(
    *,
    child_org_id: uuid.UUID,
    signing_key: str,
    ttl_minutes: int,
    now: float | None = None,
) -> str | None:
    """Build a ``<b64url-payload>.<hex-hmac>`` link code, or ``None`` if disabled.

    Returns ``None`` when no signing key is configured (feature off), so the
    caller surfaces a clear "not configured" error rather than minting an
    unverifiable code. The signature covers the base64 payload string, so the
    exact transmitted bytes are authenticated.
    """
    if not signing_key:
        return None
    issued = now if now is not None else time.time()
    payload = {
        "p": _PURPOSE,
        "c": str(child_org_id),
        "exp": int(issued) + ttl_minutes * 60,
        "jti": secrets.token_urlsafe(9),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body = _b64url_encode(raw)
    return f"{body}.{_sign(body, signing_key)}"


def verify_link_code(
    code: str | None,
    signing_key: str,
    *,
    now: float | None = None,
) -> PartnerLinkToken | None:
    """Verify signature + purpose + expiry; return the decoded facts, or ``None``.

    Returns ``None`` — never raises — on an empty key, a malformed code, a bad
    signature, a wrong purpose, a malformed payload, or an expired code, so the
    redeem endpoint can surface one opaque "invalid or expired link code" for
    every rejection path (no enumeration of which orgs have outstanding codes).
    Constant-time signature comparison via ``hmac.compare_digest``.
    """
    if not signing_key or not code or "." not in code:
        return None
    body, _, sig = code.rpartition(".")
    if not body or not sig:
        return None
    expected = _sign(body, signing_key)
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        data = json.loads(_b64url_decode(body))
        if data.get("p") != _PURPOSE:
            return None
        exp = int(data["exp"])
        decoded = PartnerLinkToken(
            child_org_id=uuid.UUID(str(data["c"])),
            jti=str(data["jti"]),
            exp=exp,
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
    current = now if now is not None else time.time()
    if exp < current:
        return None
    return decoded
