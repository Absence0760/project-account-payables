"""MFA helpers — TOTP enrollment, verification, email-OTP backup, challenge tokens.

Two factors are supported:

- **TOTP** (primary). Per-user shared secret stored on `User.mfa_secret`. Apps
  like Google Authenticator / 1Password / Authy scan the provisioning URI.
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

ALGORITHM = "HS256"

EMAIL_OTP_PREFIX = "mfa:email_otp:"
CHALLENGE_TYPE = "mfa_challenge"


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
