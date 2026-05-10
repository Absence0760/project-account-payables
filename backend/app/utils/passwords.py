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

from passlib.context import CryptContext

# Minimum constraints for a user-chosen password. The auto-generated
# temporary password (generate_temp_password) already satisfies these by
# virtue of token_urlsafe's output alphabet and length.
MIN_LENGTH = 12

pwd_context = CryptContext(
    schemes=["bcrypt_sha256", "bcrypt"],
    deprecated=["bcrypt"],
    default="bcrypt_sha256",
)

_UPPER = re.compile(r"[A-Z]")
_LOWER = re.compile(r"[a-z]")
_DIGIT = re.compile(r"[0-9]")


class PasswordError(ValueError):
    """Raised when a user-supplied password doesn't meet complexity rules."""


def generate_temp_password() -> str:
    """Generate a 16-char URL-safe temporary password for a new admin user.

    The password is sent in the welcome email and must be changed on first
    login (see User.must_change_password).
    """
    return secrets.token_urlsafe(12)  # ~16 chars after base64 encoding


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
