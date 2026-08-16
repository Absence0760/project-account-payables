"""The shared contract between the outbound Teams approval card and its webhook.

One key (``FEOH_TEAMS_SECURITY_TOKEN``), one digest, one path — used from both
ends of the Microsoft Teams approval round-trip:

* **outbound** — :func:`sign_body` stamps the approval card's ``HttpPOST`` action
  with the digest of the exact body that action will post back
  (``chat_notification_adapters/teams_adapter``);
* **inbound** — :func:`verify_body` re-derives it over the raw request bytes
  (``api/teams_approvals``). That is also, byte for byte, the shape a genuine
  Teams **Outgoing Webhook** sends, so one verifier covers both wirings.

Keeping the pair in one module is the point: a sign/verify pair that drifts is a
feature that quietly stops working, and the base64 asymmetry below is exactly the
kind of detail that drifts. Teams stores the security token **base64-encoded**
and uses its **decoded bytes** as the HMAC-SHA256 key; the digest itself travels
base64-encoded. Easy to get backwards, so it is written once.

Everything here **fails closed and never raises**: an empty security token, a
token that decodes to nothing, or a missing/garbled digest all yield ``None`` /
``False``. There is no hardcoded fallback key — an unset token means the Teams
approval feature is off in both directions (no action buttons are rendered, and
every inbound POST is rejected).

The module is **pure**: no settings import, no DB, no network. Callers pass the
security token in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

# The route the approval card's HttpPOST actions target. Declared here — beside
# the signature both ends share — rather than imported from the router, because
# the outbound adapter must not import an API module. `api/teams_approvals`
# mounts exactly this path and `tests/test_teams_card_actions.py` drift-guards
# the two against each other.
TEAMS_INTERACTIVITY_PATH = "/api/approvals/teams/interactivity"

# Header the card carries its digest on, in addition to `Authorization: HMAC …`.
# A MessageCard `HttpPOST` action may have its `Authorization` header replaced by
# Teams' own bearer token (Teams attaches the acting user's identity to an
# actionable-message POST), so the dedicated header is the reliable channel; the
# `Authorization` form is kept for a plain Outgoing-Webhook relay, which is what
# a real Teams Outgoing Webhook sends. Both carry the same digest.
CARD_SIGNATURE_HEADER = "x-feoh-card-signature"


def _hmac_key(security_token: str) -> bytes | None:
    """Decode the base64 security token into the raw HMAC key, or ``None``.

    Lenient base64 (the stdlib default, which discards non-alphabet characters)
    — deliberately, because that is what the inbound verifier already did and
    changing it would silently switch a mis-encoded deployment from "verifies
    nothing" to "feature off". A token that decodes to **empty** is treated as
    unset: keying an HMAC on b"" is not a secret.
    """
    if not security_token:
        return None
    try:
        key = base64.b64decode(security_token)
    except Exception:  # noqa: BLE001 — a malformed token must fail closed, not raise
        return None
    return key or None


def sign_body(security_token: str, raw_body: bytes) -> str | None:
    """Base64 HMAC-SHA256 digest of ``raw_body``, or ``None`` when unconfigured.

    ``None`` is the caller's signal to omit the interactive action entirely — an
    action whose signature the endpoint can never accept is worse than no action,
    because the approver would click it and be told nothing happened.
    """
    key = _hmac_key(security_token)
    if key is None:
        return None
    return base64.b64encode(hmac.new(key, raw_body, hashlib.sha256).digest()).decode("ascii")


def verify_body(security_token: str, raw_body: bytes, provided_b64: str | None) -> bool:
    """Constant-time check of a provided digest against ``raw_body``.

    False on an unset/undecodable token or an absent digest — no configuration,
    no acceptance.
    """
    if not provided_b64:
        return False
    expected = sign_body(security_token, raw_body)
    if expected is None:
        return False
    return hmac.compare_digest(expected, provided_b64)
