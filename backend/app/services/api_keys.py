"""Programmatic API-key minting + hashing helpers.

Pure, side-effect-free token primitives (no DB, no logging). The persistence
and auth resolution live in ``app/api/api_keys.py`` (management) and
``app/api/deps.py::get_api_key_principal`` (auth).

Why SHA-256 and not the bcrypt password context: API keys are high-entropy
random tokens that must be *looked up* by the presented value. A salted bcrypt
hash is deliberately un-indexable, so we store ``sha256(full_key)`` + an indexed
prefix and constant-time-compare the digest. See ``app/models/api_key.py`` for
the full rationale. This mirrors the SCIM bearer-token pattern
(``services/sso.generate_scim_token``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# Stable brand/segment prefix on every key, so a leaked token is recognisable
# as one of ours (e.g. by secret-scanners) and the format is greppable.
KEY_BRAND = "ap_live"
# How many leading characters of the full plaintext key we store as the
# (non-secret) lookup prefix shown in the management UI.
PREFIX_LEN = 16


def generate_api_key() -> tuple[str, str, str]:
    """Mint a new API key.

    Returns ``(full_key, key_prefix, key_hash)``:
      * ``full_key`` — the plaintext, e.g. ``ap_live_<43 url-safe chars>``.
        Shown to the admin EXACTLY ONCE and never persisted or logged.
      * ``key_prefix`` — the first ``PREFIX_LEN`` chars, stored + indexed for
        lookup and shown in the UI. Not a secret on its own.
      * ``key_hash`` — ``sha256(full_key)`` hex digest, the only persisted form
        of the key material.
    """
    secret = secrets.token_urlsafe(32)
    full_key = f"{KEY_BRAND}_{secret}"
    return full_key, full_key[:PREFIX_LEN], hash_api_key(full_key)


def hash_api_key(full_key: str) -> str:
    """sha256 hex digest of a plaintext key — the persisted/looked-up form."""
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def key_prefix_of(full_key: str) -> str:
    """The indexed lookup prefix for a presented plaintext key."""
    return full_key[:PREFIX_LEN]


def constant_time_equals(a: str, b: str) -> bool:
    """Constant-time digest comparison (defends against timing oracles)."""
    return hmac.compare_digest(a, b)
