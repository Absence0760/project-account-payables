"""WebAuthn / passkey MFA — registration + authentication ceremonies.

A passkey is an ADDITIONAL second factor alongside the TOTP/email-OTP flow in
``services/mfa.py`` (which keeps working unchanged). This module owns the
separate WebAuthn code path: it wraps the maintained ``py_webauthn`` library to

  * mint **registration** options (``begin_registration``) and verify the
    browser's ``navigator.credentials.create()`` response
    (``finish_registration``) — persisting a ``WebAuthnCredential`` row;
  * mint **authentication** options (``begin_authentication``) and verify the
    browser's ``navigator.credentials.get()`` response
    (``finish_authentication``) — bumping the credential's signature counter
    (clone-detection) on success.

The per-ceremony random challenge is server-minted and stashed in Redis under a
short TTL keyed to the user, so the verify call can't be fed an attacker-chosen
challenge. Nothing here logs credential material; the WebAuthn public key is not
a secret in the password sense (the private key never leaves the authenticator),
so there is no bcrypt/sops concern — but we still keep it out of logs.

RP ID / allowed origins are configurable (``FEOH_WEBAUTHN_RP_ID`` /
``FEOH_WEBAUTHN_ORIGINS``) so the same code runs on ``localhost`` in dev and a
real apex in production. Local-first: the defaults work on
``*.localhost:7777`` with no cloud account.
"""

from __future__ import annotations

import json
import uuid

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import settings
from app.redis import get_redis

# Redis key namespaces for the per-ceremony challenge. Distinct prefixes keep a
# registration challenge from satisfying an authentication verify and vice
# versa. Keyed by user id — one outstanding challenge per user per ceremony.
_REG_CHALLENGE_PREFIX = "webauthn:reg_challenge:"
_AUTH_CHALLENGE_PREFIX = "webauthn:auth_challenge:"
# Step-up assertions get their OWN namespace, further partitioned by the
# operation being authorized — see `_assertion_challenge_key`.
_STEP_UP_CHALLENGE_PREFIX = "webauthn:stepup_challenge:"

# The two things an assertion (``navigator.credentials.get()``) can be for.
# `clientDataJSON.type` is `"webauthn.get"` for BOTH — the authenticator has no
# idea what the RP intends to do with the signature — so the *challenge itself*
# is the only thing that can bind an assertion to its purpose. That makes the
# Redis namespace a security boundary, not bookkeeping: a login challenge and a
# step-up challenge are minted into different, single-use slots, and each verify
# path reads only its own slot. An assertion signed over a step-up challenge
# therefore cannot satisfy `/mfa/passkey/authenticate/verify` (which looks for a
# login challenge and finds a different value), and a login assertion cannot
# satisfy a step-up. Step-up slots additionally carry the operation, so an
# assertion obtained to authorize "register a passkey" can't be redirected into
# "delete a passkey".
ASSERTION_PURPOSE_LOGIN = "login"
ASSERTION_PURPOSE_STEP_UP = "step_up"


class WebAuthnError(Exception):
    """Raised on any verification failure (bad signature, wrong challenge,
    origin mismatch, replayed counter). Carries a non-PII message safe to
    surface to the client as a generic 400/401."""


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _allowed_origins() -> list[str]:
    raw = settings.webauthn_origins or ""
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["http://localhost:7777"]


def _verify_origin_ok(seen_origin: str) -> bool:
    """py_webauthn's verify takes a single expected_origin (or list, version
    dependent). We pre-check against our allowed set so a multi-tenant /
    multi-origin deployment works regardless of the library's list support."""
    return seen_origin in _allowed_origins()


def _redis_key(prefix: str, user_id: uuid.UUID) -> str:
    return f"{prefix}{user_id}"


def _assertion_challenge_key(user_id: uuid.UUID, *, purpose: str, operation: str | None) -> str:
    """Redis slot an assertion challenge lives in.

    Login keeps the historical key (`webauthn:auth_challenge:<user_id>`).
    Step-up gets a distinct prefix plus the operation, so each (user, purpose,
    operation) triple has its own single-use slot and none of them can be
    consumed by another path. `operation` is mandatory for a step-up — a
    step-up challenge with no operation would be replayable across every
    factor-management endpoint, which is exactly what this partitioning exists
    to prevent.
    """
    if purpose == ASSERTION_PURPOSE_LOGIN:
        return _redis_key(_AUTH_CHALLENGE_PREFIX, user_id)
    if purpose != ASSERTION_PURPOSE_STEP_UP:
        raise WebAuthnError("Unknown assertion purpose")
    if not operation:
        raise WebAuthnError("Step-up assertion requires an operation")
    return f"{_STEP_UP_CHALLENGE_PREFIX}{operation}:{user_id}"


# ---------------------------------------------------------------------------
# Registration ceremony
# ---------------------------------------------------------------------------


async def begin_registration(
    *,
    user_id: uuid.UUID,
    user_name: str,
    user_display_name: str,
    existing_credential_ids: list[str],
) -> str:
    """Mint registration options the browser feeds to
    ``navigator.credentials.create()``. Returns the options as a JSON string
    (already in the WebAuthn wire shape — base64url fields etc.).

    The server-minted challenge is stashed in Redis (short TTL) so
    ``finish_registration`` verifies against a value the client never chose.
    ``existing_credential_ids`` are excluded so a user can't double-register the
    same authenticator.
    """
    exclude = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(cid)) for cid in existing_credential_ids
    ]
    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=str(user_id).encode("utf-8"),
        user_name=user_name,
        user_display_name=user_display_name,
        exclude_credentials=exclude,
        # Prefer a platform/passkey authenticator with user verification, but
        # allow roaming security keys too (no authenticator_attachment pin).
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    r = await get_redis()
    await r.setex(
        _redis_key(_REG_CHALLENGE_PREFIX, user_id),
        settings.webauthn_challenge_ttl_seconds,
        bytes_to_base64url(options.challenge),
    )
    return options_to_json(options)


async def finish_registration(
    *,
    user_id: uuid.UUID,
    credential_json: str,
) -> dict:
    """Verify the browser's ``create()`` response against the stashed challenge.

    On success returns a dict of the row fields to persist:
    ``{credential_id, public_key, sign_count, transports}`` (all base64url where
    binary). Raises ``WebAuthnError`` on any failure. The challenge is consumed
    (single-use) regardless of outcome so a captured response can't be replayed.
    """
    r = await get_redis()
    key = _redis_key(_REG_CHALLENGE_PREFIX, user_id)
    stored = await r.get(key)
    await r.delete(key)  # single-use: consume even on the failure path
    if not stored:
        raise WebAuthnError("Registration challenge expired or missing")
    expected_challenge = base64url_to_bytes(
        stored.decode("utf-8") if isinstance(stored, bytes) else stored
    )

    seen_origin = _extract_origin(credential_json)
    if not seen_origin or not _verify_origin_ok(seen_origin):
        raise WebAuthnError("Origin not allowed")

    try:
        verified = verify_registration_response(
            credential=credential_json,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=seen_origin,
            require_user_verification=False,
        )
    except InvalidRegistrationResponse as exc:
        raise WebAuthnError("Registration verification failed") from exc

    transports = _extract_transports(credential_json)
    return {
        "credential_id": bytes_to_base64url(verified.credential_id),
        "public_key": bytes_to_base64url(verified.credential_public_key),
        "sign_count": verified.sign_count,
        "transports": ",".join(transports) if transports else None,
    }


# ---------------------------------------------------------------------------
# Authentication ceremony
# ---------------------------------------------------------------------------


async def begin_authentication(
    *,
    user_id: uuid.UUID,
    credentials: list[dict],
    purpose: str,
    operation: str | None = None,
) -> str:
    """Mint authentication options for ``navigator.credentials.get()``.

    ``credentials`` is the list of the user's registered passkeys as
    ``{credential_id, transports}`` dicts. They become the ``allowCredentials``
    list so the browser surfaces the right authenticator. The challenge is
    stashed in Redis keyed to the user AND to what the assertion will be allowed
    to authorize (``purpose`` / ``operation`` — see
    ``_assertion_challenge_key``). ``purpose`` has no default on purpose: an
    assertion that is valid for two things at once is a real vulnerability, so
    every caller has to say which one it wants.
    """
    allow = []
    for c in credentials:
        transports = None
        if c.get("transports"):
            transports = [
                AuthenticatorTransport(t)
                for t in c["transports"].split(",")
                if t in AuthenticatorTransport._value2member_map_
            ]
        allow.append(
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(c["credential_id"]),
                transports=transports,
            )
        )
    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    r = await get_redis()
    await r.setex(
        _assertion_challenge_key(user_id, purpose=purpose, operation=operation),
        settings.webauthn_challenge_ttl_seconds,
        bytes_to_base64url(options.challenge),
    )
    return options_to_json(options)


async def finish_authentication(
    *,
    user_id: uuid.UUID,
    credential_json: str,
    stored_public_key: str,
    stored_sign_count: int,
    purpose: str,
    operation: str | None = None,
) -> int:
    """Verify the browser's ``get()`` response. Returns the new signature
    counter to persist on success; raises ``WebAuthnError`` otherwise. The
    challenge is consumed single-use.

    ``purpose`` / ``operation`` must match the ones the challenge was minted
    under — they select the Redis slot, so an assertion produced for a different
    purpose simply doesn't match the value stored here and fails like any other
    bad signature.

    The caller resolves which ``WebAuthnCredential`` row to pass in by matching
    the response's credential id (``extract_credential_id``) before calling this.
    """
    r = await get_redis()
    key = _assertion_challenge_key(user_id, purpose=purpose, operation=operation)
    stored = await r.get(key)
    await r.delete(key)
    if not stored:
        raise WebAuthnError("Authentication challenge expired or missing")
    expected_challenge = base64url_to_bytes(
        stored.decode("utf-8") if isinstance(stored, bytes) else stored
    )

    seen_origin = _extract_origin(credential_json)
    if not seen_origin or not _verify_origin_ok(seen_origin):
        raise WebAuthnError("Origin not allowed")

    try:
        verified = verify_authentication_response(
            credential=credential_json,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=seen_origin,
            credential_public_key=base64url_to_bytes(stored_public_key),
            credential_current_sign_count=stored_sign_count,
            require_user_verification=False,
        )
    except InvalidAuthenticationResponse as exc:
        # py_webauthn raises this on a counter regression (cloned authenticator)
        # as well as a bad signature — both are auth failures.
        raise WebAuthnError("Authentication verification failed") from exc

    return verified.new_sign_count


# ---------------------------------------------------------------------------
# Response parsing helpers (pure, no Redis/DB) — exposed for the route layer
# ---------------------------------------------------------------------------


def extract_credential_id(credential_json: str) -> str | None:
    """Pull the base64url credential id out of a browser ceremony response so
    the route can look up the matching ``WebAuthnCredential`` row. Returns None
    on a malformed body (the caller treats that as a generic auth failure)."""
    try:
        data = json.loads(credential_json)
    except (ValueError, TypeError):
        return None
    cid = data.get("id") or data.get("rawId")
    return cid if isinstance(cid, str) and cid else None


def _extract_origin(credential_json: str) -> str | None:
    """Read ``response.clientDataJSON.origin`` from the ceremony response. The
    library also checks origin, but we pre-screen against the allowed set to
    support multiple tenant-subdomain origins."""
    try:
        data = json.loads(credential_json)
        client_data_b64 = data["response"]["clientDataJSON"]
        client_data = json.loads(base64url_to_bytes(client_data_b64).decode("utf-8"))
        origin = client_data.get("origin")
        return origin if isinstance(origin, str) else None
    except (ValueError, TypeError, KeyError):
        return None


def _extract_transports(credential_json: str) -> list[str]:
    try:
        data = json.loads(credential_json)
        transports = data.get("response", {}).get("transports")
        if isinstance(transports, list):
            return [t for t in transports if isinstance(t, str)]
    except (ValueError, TypeError):
        pass
    return []
