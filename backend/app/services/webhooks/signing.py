"""Per-subscription signing secret + payload signature.

The signature reuses ``webhook_security.verify_hmac_sha256``'s exact primitive
(HMAC-SHA256 over the raw bytes, hex digest) so the inbound-verify discipline
and the outbound-sign discipline are byte-for-byte symmetric — a customer who
already verifies our inbound style verifies these the same way.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# Stable brand on every signing secret so a leaked one is recognisable as ours
# (greppable, secret-scanner-friendly) — mirrors the ``ap_live`` API-key brand.
SECRET_BRAND = "whsec"
# Leading chars of the secret stored + shown in the management UI (non-secret).
SECRET_PREFIX_LEN = 12


def generate_signing_secret() -> tuple[str, str]:
    """Mint a webhook signing secret.

    Returns ``(secret, secret_prefix)``. The full secret is returned to the
    admin EXACTLY ONCE at subscription-create time and then stored verbatim so
    the dispatcher can sign with it (an HMAC verification key is symmetric — see
    ``app/models/webhook.py``). The prefix is the non-secret lookup/label shown
    in list/get responses.
    """
    secret = f"{SECRET_BRAND}_{secrets.token_urlsafe(32)}"
    return secret, secret[:SECRET_PREFIX_LEN]


def sign_payload(secret: str, raw_body: bytes) -> str:
    """HMAC-SHA256 hex digest of ``raw_body`` under ``secret``.

    The dispatcher sends this in the ``X-Webhook-Signature`` header; the
    receiver re-derives it with ``hmac.new(secret, body, sha256).hexdigest()``
    and compares constant-time — exactly the inbound primitive in
    ``webhook_security.verify_hmac_sha256``.
    """
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
