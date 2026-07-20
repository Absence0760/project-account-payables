"""MFA helpers — TOTP enrollment, verification, email-OTP backup, challenge tokens.

Two factors are supported:

- **TOTP** (primary). Per-user shared secret stored on `User.mfa_secret`. Apps
  like Google Authenticator / 1Password / Authy scan the provisioning URI. A
  *candidate* secret from an in-flight enrollment is NOT written to the
  account — it waits in Redis (`stash_pending_totp_secret`) until the user
  proves they hold it, so starting an enrollment never disturbs the factor
  already in force.
- **Email OTP** (backup). Sent on demand to the account email when the user
  can't access their authenticator. The 6-digit code lives in Redis with a
  short TTL — no DB column needed.

The post-password "still need MFA" challenge is itself a short-lived JWT.
That keeps the login flow stateless: backend hands the browser a token,
browser submits TOTP/email-OTP with it, backend verifies + mints a real
access token. No DB row to garbage-collect.

Org-level enforcement is read from `Organization.settings.mfa.required`. When
true, users without enrolled MFA can still log in once but are gated until
they enroll (handled in the auth router).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import qrcode
from jose import JWTError, jwt

from app.config import settings
from app.redis import get_redis
from app.utils.passwords import pwd_context

ALGORITHM = "HS256"

EMAIL_OTP_PREFIX = "mfa:email_otp:"
# Pending (started-but-not-yet-verified) TOTP enrollment secrets. Separate
# keyspaces per surface, same reason as the email-OTP prefixes below: an
# employee user id and a vendor user id must never share a slot.
PENDING_ENROLL_PREFIX = "mfa:pending_enroll:"
VENDOR_PENDING_ENROLL_PREFIX = "mfa:vendor_pending_enroll:"
# Supplier-portal email-OTP backup. A DISTINCT Redis keyspace from the employee
# one (`mfa:email_otp:`) so a vendor user id can never collide with an employee
# user id of the same UUID value — the same isolation principle as the distinct
# `vendor_mfa_challenge` token type.
VENDOR_EMAIL_OTP_PREFIX = "mfa:vendor_email_otp:"
CHALLENGE_TYPE = "mfa_challenge"
# Supplier-portal MFA challenge. A DISTINCT `typ` from both the employee
# challenge (`mfa_challenge`) and the vendor access token (`vendor`), so a
# vendor MFA challenge token can never resolve as an access token through
# `get_current_vendor_user`, nor be confused with the employee flow.
VENDOR_CHALLENGE_TYPE = "vendor_mfa_challenge"


# ---------------------------------------------------------------------------
# Org enforcement
# ---------------------------------------------------------------------------


def org_requires_mfa(org_settings: dict | None) -> bool:
    """True when the org has flipped on tenant-wide MFA enforcement."""
    if not settings.mfa_enabled:
        return False
    if not org_settings:
        return False
    mfa = org_settings.get("mfa") or {}
    return bool(mfa.get("required"))


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


def generate_totp_secret() -> str:
    """Mint a fresh base32 TOTP secret. 160 bits of entropy = RFC 6238 default."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_label: str, issuer: str | None = None) -> str:
    """Build the otpauth:// URI authenticator apps consume from a QR code."""
    return pyotp.TOTP(secret).provisioning_uri(
        name=account_label,
        issuer_name=issuer or settings.mfa_issuer,
    )


def qr_code_data_url(uri: str) -> str:
    """Render a QR code for the provisioning URI as a base64 data URL.

    Returning a data URL keeps the frontend dead simple — no separate image
    endpoint, no auth header juggling for an <img src>. The QR is small
    (~1KB) so the inline cost is negligible.
    """
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def verify_totp(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP. Allows ±1 30-second window for clock skew."""
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pending enrollment secret — the live factor is never disturbed
#
# TOTP enrollment is a two-step ceremony (mint a secret + QR, then prove you
# scanned it). Writing the candidate secret onto the account at step one meant
# merely *starting* an enrollment stripped whatever second factor was already
# in force — a leaked access token was enough to silently downgrade an account
# to single-factor. The candidate now waits here instead, under a short TTL,
# next to the WebAuthn ceremony challenge; only `/mfa/enroll/verify` promotes
# it onto the account.
#
# The value is the raw base32 seed (it has to be, to verify a code against it)
# — the same material `users.mfa_secret` already holds, just for minutes
# rather than years. It is never logged and never returned outside the
# enrollment-start response.
# ---------------------------------------------------------------------------


def _pending_enroll_key(prefix: str, subject_id: uuid.UUID) -> str:
    return f"{prefix}{subject_id}"


async def _stash_pending_secret(prefix: str, subject_id: uuid.UUID, secret: str) -> None:
    r = await get_redis()
    await r.setex(
        _pending_enroll_key(prefix, subject_id),
        settings.mfa_enroll_pending_ttl_seconds,
        secret,
    )


async def _read_pending_secret(prefix: str, subject_id: uuid.UUID) -> str | None:
    r = await get_redis()
    stored = await r.get(_pending_enroll_key(prefix, subject_id))
    if not stored:
        return None
    return stored.decode("utf-8") if isinstance(stored, bytes) else stored


async def _clear_pending_secret(prefix: str, subject_id: uuid.UUID) -> None:
    r = await get_redis()
    await r.delete(_pending_enroll_key(prefix, subject_id))


async def stash_pending_totp_secret(user_id: uuid.UUID, secret: str) -> None:
    """Hold a candidate enrollment secret for an employee user. Replaces any
    previous outstanding candidate — one enrollment in flight per user."""
    await _stash_pending_secret(PENDING_ENROLL_PREFIX, user_id, secret)


async def read_pending_totp_secret(user_id: uuid.UUID) -> str | None:
    """The employee user's in-flight candidate secret, or None if enrollment
    was never started (or has expired)."""
    return await _read_pending_secret(PENDING_ENROLL_PREFIX, user_id)


async def clear_pending_totp_secret(user_id: uuid.UUID) -> None:
    """Drop the employee candidate — called once it's promoted onto the account."""
    await _clear_pending_secret(PENDING_ENROLL_PREFIX, user_id)


async def stash_pending_vendor_totp_secret(vendor_user_id: uuid.UUID, secret: str) -> None:
    """Supplier-portal counterpart of `stash_pending_totp_secret`."""
    await _stash_pending_secret(VENDOR_PENDING_ENROLL_PREFIX, vendor_user_id, secret)


async def read_pending_vendor_totp_secret(vendor_user_id: uuid.UUID) -> str | None:
    """Supplier-portal counterpart of `read_pending_totp_secret`."""
    return await _read_pending_secret(VENDOR_PENDING_ENROLL_PREFIX, vendor_user_id)


async def clear_pending_vendor_totp_secret(vendor_user_id: uuid.UUID) -> None:
    """Supplier-portal counterpart of `clear_pending_totp_secret`."""
    await _clear_pending_secret(VENDOR_PENDING_ENROLL_PREFIX, vendor_user_id)


# ---------------------------------------------------------------------------
# Step-up re-authentication
# ---------------------------------------------------------------------------


def step_up_available(*, hashed_password: str | None, mfa_secret: str | None) -> bool:
    """Does the account hold a credential a step-up can actually challenge?

    False only for an account with neither a password nor a TOTP secret — an
    SSO-only user whose sole factor is a passkey. Demanding a step-up there
    would permanently lock them out of managing their own factors (they have
    nothing to type), so the caller lets those through. The durable answer for
    that case is a WebAuthn assertion as the step-up; until then the IdP is
    that account's authentication authority anyway. Every password-backed
    account — the overwhelming majority — is fully covered.
    """
    return bool(hashed_password) or bool(mfa_secret)


def step_up_verified(
    *,
    hashed_password: str | None,
    mfa_secret: str | None,
    password: str | None,
    code: str | None,
) -> bool:
    """Did the caller re-prove control of the account?

    Used before an account's *existing* second factor can be replaced (TOTP
    re-enrollment, adding a passkey). Either credential satisfies it: the
    account password — the same check `/mfa/disable` makes — or a code from
    the authenticator currently enrolled. Both are things a bearer token alone
    does not grant, which is the point: an attacker holding a stolen session
    must not be able to swap the second factor out from under the owner.

    Shared by the employee and supplier-portal surfaces so the two can't drift.
    Returns a plain bool; the caller decides the status code. Password
    comparison goes through the shared `pwd_context` (bcrypt_sha256), TOTP
    through `verify_totp` — both constant-time internally.
    """
    if password and hashed_password and pwd_context.verify(password, hashed_password):
        return True
    if code and mfa_secret and verify_totp(mfa_secret, code):
        return True
    return False


# ---------------------------------------------------------------------------
# Email OTP (backup)
# ---------------------------------------------------------------------------


def _email_otp_key(user_id: uuid.UUID) -> str:
    return f"{EMAIL_OTP_PREFIX}{user_id}"


def _hash_otp(code: str) -> str:
    """SHA-256 the OTP before storing — Redis dumps shouldn't reveal codes."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


async def issue_email_otp(user_id: uuid.UUID) -> str:
    """Generate a 6-digit email backup code, store its hash in Redis, return plaintext.

    Caller is responsible for actually sending the code via the email adapter.
    Each call invalidates any previous outstanding code for the user.
    """
    code = f"{secrets.randbelow(1_000_000):06d}"
    r = await get_redis()
    await r.setex(_email_otp_key(user_id), settings.mfa_email_otp_ttl_seconds, _hash_otp(code))
    return code


async def verify_email_otp(user_id: uuid.UUID, code: str) -> bool:
    """Verify + consume a previously-issued email OTP. Single-use."""
    if not code:
        return False
    r = await get_redis()
    key = _email_otp_key(user_id)
    stored = await r.get(key)
    if not stored:
        return False
    stored_hex = stored.decode("utf-8") if isinstance(stored, bytes) else stored
    # constant-time compare to thwart timing oracles
    if not hmac.compare_digest(stored_hex, _hash_otp(code.strip())):
        return False
    await r.delete(key)
    return True


# Supplier-portal email-OTP backup — same mechanism as the employee one above,
# but in a distinct Redis keyspace (`VENDOR_EMAIL_OTP_PREFIX`) so a vendor-user
# id and an employee-user id of the same UUID value never share a slot.


def _vendor_email_otp_key(vendor_user_id: uuid.UUID) -> str:
    return f"{VENDOR_EMAIL_OTP_PREFIX}{vendor_user_id}"


async def issue_vendor_email_otp(vendor_user_id: uuid.UUID) -> str:
    """Generate a 6-digit email backup code for a supplier-portal user, store
    its hash in Redis, return the plaintext for the caller to email. Each call
    invalidates any previous outstanding code for that vendor user."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    r = await get_redis()
    await r.setex(
        _vendor_email_otp_key(vendor_user_id),
        settings.mfa_email_otp_ttl_seconds,
        _hash_otp(code),
    )
    return code


async def verify_vendor_email_otp(vendor_user_id: uuid.UUID, code: str) -> bool:
    """Verify + consume a previously-issued supplier-portal email OTP. Single-use."""
    if not code:
        return False
    r = await get_redis()
    key = _vendor_email_otp_key(vendor_user_id)
    stored = await r.get(key)
    if not stored:
        return False
    stored_hex = stored.decode("utf-8") if isinstance(stored, bytes) else stored
    if not hmac.compare_digest(stored_hex, _hash_otp(code.strip())):
        return False
    await r.delete(key)
    return True


# ---------------------------------------------------------------------------
# Challenge token (short-lived JWT issued after password check)
# ---------------------------------------------------------------------------


def create_challenge_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(UTC) + timedelta(seconds=settings.mfa_challenge_ttl_seconds)
    payload = {
        "sub": str(user_id),
        "typ": CHALLENGE_TYPE,
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_challenge_token(token: str) -> uuid.UUID:
    """Verify a challenge token and return the user_id. Raises ValueError on bad tokens."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError("Invalid or expired MFA challenge token") from exc
    if payload.get("typ") != CHALLENGE_TYPE:
        raise ValueError("Wrong token type for MFA challenge")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Malformed MFA challenge token") from exc


# ---------------------------------------------------------------------------
# Vendor (supplier-portal) MFA challenge token — same shape, distinct `typ`
# ---------------------------------------------------------------------------


def create_vendor_challenge_token(vendor_user_id: uuid.UUID) -> str:
    """Short-lived JWT proving a supplier-portal password was accepted but MFA
    is still owed. `typ=vendor_mfa_challenge` keeps it out of every other auth
    path (employee challenge, vendor access token)."""
    expire = datetime.now(UTC) + timedelta(seconds=settings.mfa_challenge_ttl_seconds)
    payload = {
        "sub": str(vendor_user_id),
        "typ": VENDOR_CHALLENGE_TYPE,
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_vendor_challenge_token(token: str) -> uuid.UUID:
    """Verify a vendor MFA challenge token and return the vendor_user_id.
    Raises ValueError on bad tokens (wrong type, expired, malformed)."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError("Invalid or expired MFA challenge token") from exc
    if payload.get("typ") != VENDOR_CHALLENGE_TYPE:
        raise ValueError("Wrong token type for MFA challenge")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Malformed MFA challenge token") from exc
