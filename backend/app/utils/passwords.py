"""Password generation, complexity checks, and the shared hash context.

The hash context lives here (not duplicated in every router that hashes
passwords) so we get one consistent algorithm choice across the
codebase. `bcrypt_sha256` pre-hashes the input with SHA-256 before
running bcrypt, which side-steps bcrypt's 72-byte truncation — a user
who picks a 100-char password is fully protected by the suffix. The
legacy `bcrypt` scheme is kept in the schemes list so existing
`$2b$...` hashes still verify; new hashes are emitted as
`$bcrypt-sha256$...` and the deprecated="auto" policy will re-hash on
verify when a user with a legacy hash next logs in.
"""

from __future__ import annotations

import re
import secrets
import string

from passlib.context import CryptContext

# Minimum constraints for a user-chosen password. The auto-generated
# temporary password (generate_temp_password) is constructed to satisfy
# these deterministically — see that function.
MIN_LENGTH = 12

pwd_context = CryptContext(
    schemes=["bcrypt_sha256", "bcrypt"],
    deprecated=["bcrypt"],
    default="bcrypt_sha256",
)

_UPPER = re.compile(r"[A-Z]")
_LOWER = re.compile(r"[a-z]")
_DIGIT = re.compile(r"[0-9]")

# A bcrypt_sha256 hash of a fixed throwaway secret, computed once at import.
# Used to equalize login timing — see `dummy_verify`.
_DUMMY_HASH = pwd_context.hash("timing-equalizer-not-a-real-secret")


def dummy_verify() -> None:
    """Run a throwaway password verification to match the wall-clock cost of a
    real `pwd_context.verify`.

    Login handlers must call this on the user-not-found branch: otherwise the
    not-found path returns ~100ms faster than a wrong-password path (which runs
    bcrypt), letting an attacker time the difference to enumerate which emails
    have accounts. The result is intentionally ignored.
    """
    pwd_context.verify("x", _DUMMY_HASH)


class PasswordError(ValueError):
    """Raised when a user-supplied password doesn't meet complexity rules."""


def generate_temp_password() -> str:
    """Generate a 16-char temporary password for a new admin user.

    Sent in the welcome email and must be changed on first login (see
    User.must_change_password). Constructed to ALWAYS satisfy
    validate_password_complexity — at least one uppercase letter, one lowercase
    letter, and one digit, length 16 (>= MIN_LENGTH) — so a new user's first
    login is never bounced by the complexity gate. (The previous
    `secrets.token_urlsafe(12)` relied on random-byte distribution and could
    emit a password with no digit or no case mix.) The alphabet is alphanumeric,
    a subset of the URL-safe set, so the password stays safe in the welcome URL.
    """
    alphabet = string.ascii_letters + string.digits
    # Seed one character of each required class so complexity is guaranteed,
    # fill the rest from the full alphanumeric alphabet, then shuffle so the
    # seeded positions aren't predictable.
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        *(secrets.choice(alphabet) for _ in range(13)),
    ]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def validate_password_complexity(password: str) -> None:
    """Enforce minimum complexity for user-chosen passwords. Raises PasswordError."""
    if len(password) < MIN_LENGTH:
        raise PasswordError(f"Password must be at least {MIN_LENGTH} characters.")
    if not _UPPER.search(password):
        raise PasswordError("Password must contain an uppercase letter.")
    if not _LOWER.search(password):
        raise PasswordError("Password must contain a lowercase letter.")
    if not _DIGIT.search(password):
        raise PasswordError("Password must contain a digit.")
