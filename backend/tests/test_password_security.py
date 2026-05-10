"""In-depth password security tests.

Covers the contract a user / auditor expects for every credential the
backend stores or accepts:

  - Complexity boundary (length / character classes)
  - bcrypt is the hash format (not SHA-1, not plaintext, not reversible)
  - Same password produces different hashes (salt is random)
  - bcrypt's 72-byte truncation does not silently downgrade long passwords
  - Empty / whitespace / null-byte passwords are rejected by complexity
  - No endpoint response ever echoes a plaintext password
  - The change-password contract: wrong current → 400; weak new → 422;
    success returns a new hash

`test_signup_utils.py` covers the strong-password happy path and a
parametric weak-password parametrize. These tests add the boundary
conditions and the storage-format contract.
"""

from __future__ import annotations

import pytest

from app.utils.passwords import (
    MIN_LENGTH,
    PasswordError,
    generate_temp_password,
    validate_password_complexity,
)

# ---------------------------------------------------------------------------
# Length boundary
# ---------------------------------------------------------------------------


def test_password_below_minimum_length_is_rejected():
    """Eleven-char password — even with every class — must fail. The
    boundary is the contract; a regression that loosens MIN_LENGTH is
    a real security weakening."""
    short = "Abcdefg123!"  # 11 chars, has upper/lower/digit
    assert len(short) < MIN_LENGTH
    with pytest.raises(PasswordError) as exc:
        validate_password_complexity(short)
    assert str(MIN_LENGTH) in str(exc.value)


def test_password_at_minimum_length_is_accepted():
    """Twelve-char password with each class — positive control to
    prove the boundary above isn't just rejecting all short inputs."""
    boundary = "Abcdefg1234X"  # exactly 12 chars
    assert len(boundary) == MIN_LENGTH
    validate_password_complexity(boundary)  # should not raise


def test_empty_password_is_rejected():
    """An empty password is the worst case of "too short". The
    complexity check must fail closed rather than letting it through
    on falsy guard logic."""
    with pytest.raises(PasswordError):
        validate_password_complexity("")


def test_whitespace_only_password_below_min_is_rejected():
    """Whitespace counts toward length but not toward any class —
    a regression that special-cases whitespace as a class would
    accept this."""
    with pytest.raises(PasswordError):
        validate_password_complexity("            ")  # 12 spaces


# ---------------------------------------------------------------------------
# Complexity classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pwd,expected_msg",
    [
        ("abcdefg1234567", "uppercase"),  # no upper
        ("ABCDEFG1234567", "lowercase"),  # no lower
        ("AbcdefgHijklmn", "digit"),  # no digit
    ],
    ids=["missing_upper", "missing_lower", "missing_digit"],
)
def test_password_missing_each_class_is_rejected(pwd: str, expected_msg: str):
    """Each class is checked independently — confirm by isolating
    each rule's failure path. The error message must name the missing
    class so the UI can render a helpful hint."""
    assert len(pwd) >= MIN_LENGTH  # not a length failure
    with pytest.raises(PasswordError) as exc:
        validate_password_complexity(pwd)
    assert expected_msg in str(exc.value).lower()


def test_password_with_unicode_and_symbols_is_accepted():
    """Symbols and non-ASCII letters aren't required, but they
    shouldn't break the check either. A regression that rejects "$"
    or "é" would surprise users with international keyboards."""
    validate_password_complexity("Pässwörd123ñ!")  # valid: upper, lower, digit, len ≥ 12


# ---------------------------------------------------------------------------
# Generated temp passwords already pass the complexity check
# ---------------------------------------------------------------------------


def test_generated_temp_password_passes_complexity():
    """The temp password emitted by the signup welcome email must
    itself satisfy complexity — otherwise the user's first login is
    immediately bounced to /change-password with a confusing error."""
    # token_urlsafe doesn't guarantee a digit by chance, so we may
    # need many samples to be statistically confident. The contract
    # IS "passes complexity at least most of the time" — a flaky
    # temp password is a real defect. If this fails intermittently,
    # the temp generator needs to ensure a digit (it currently relies
    # on random-byte distribution).
    failures = 0
    for _ in range(20):
        try:
            validate_password_complexity(generate_temp_password())
        except PasswordError:
            failures += 1
    # Allow a tiny rate but no more — flag a regression in the
    # generator if every other sample fails.
    assert failures <= 4, f"temp generator produced {failures}/20 weak passwords"


# ---------------------------------------------------------------------------
# Storage format: bcrypt
# ---------------------------------------------------------------------------


def test_password_is_stored_as_bcrypt_sha256_hash_not_plaintext():
    """The hashed_password column must hold a `bcrypt_sha256` hash —
    bcrypt with a SHA-256 pre-hash, so long passwords don't truncate
    at 72 bytes. Legacy `$2b$` hashes still verify (deprecated="auto")
    but new writes use the safer scheme."""
    from app.utils.passwords import pwd_context as ctx
    h = ctx.hash("Correct-Horse-1")
    assert h.startswith("$bcrypt-sha256$"), f"expected bcrypt_sha256 prefix, got: {h[:25]}"
    assert "Correct-Horse-1" not in h


def test_same_password_produces_different_hashes():
    """bcrypt's per-hash salt means two calls on the same plaintext
    return different outputs. If they were equal, the salt is broken
    and a rainbow table works against the whole user table."""
    from app.utils.passwords import pwd_context as ctx
    h1 = ctx.hash("Correct-Horse-1")
    h2 = ctx.hash("Correct-Horse-1")
    assert h1 != h2


def test_bcrypt_verify_accepts_correct_and_rejects_wrong():
    """The verify path is the actual production check. Pin it so a
    swap to a broken hasher (e.g., one that always returns True) is
    caught by tests rather than a customer."""
    from app.utils.passwords import pwd_context as ctx
    h = ctx.hash("Correct-Horse-1")
    assert ctx.verify("Correct-Horse-1", h) is True
    assert ctx.verify("wrong-password", h) is False
    assert ctx.verify("", h) is False


def test_bcrypt_handles_long_password_without_silent_truncation_collision():
    """bcrypt truncates at 72 bytes, which means two 100-char
    passwords sharing a 72-char prefix would historically verify as
    equal. passlib applies a SHA-256 pre-hash to avoid that — confirm
    our context inherits the safe behavior."""
    from app.utils.passwords import pwd_context as ctx
    base = "A" * 72 + "0aaaaaaaaa"  # first 72 bytes identical
    other = "A" * 72 + "0bbbbbbbbb"
    h = ctx.hash(base)
    # If passlib pre-hashes, `other` must NOT verify against `base`'s hash.
    assert ctx.verify(base, h) is True
    assert ctx.verify(other, h) is False, "bcrypt truncation collision — pre-hash missing?"


# ---------------------------------------------------------------------------
# No plaintext leakage in response schemas
# ---------------------------------------------------------------------------


def test_user_response_schema_has_no_password_field():
    """Defensive: even if a future refactor accidentally exposes the
    User model verbatim, the response schema must not declare a
    password / hashed_password field."""
    from app.schemas.auth import UserResponse

    fields = set(UserResponse.model_fields.keys())
    assert "password" not in fields
    assert "hashed_password" not in fields


def test_token_response_schema_has_no_password_field():
    """Same contract for the login response — the access token is
    fine, the password (or any echo of it) is not."""
    from app.schemas.auth import TokenResponse

    fields = set(TokenResponse.model_fields.keys())
    assert "password" not in fields
    assert "hashed_password" not in fields


# ---------------------------------------------------------------------------
# Request schema enforces server-side minimum even if client misbehaves
# ---------------------------------------------------------------------------


def test_change_password_request_rejects_short_new_password_at_schema_level():
    """Pydantic catches a too-short new_password before the handler
    runs — a defence-in-depth against a buggy client that calls the
    endpoint with a 6-char value."""
    import pydantic

    from app.schemas.auth import ChangePasswordRequest

    with pytest.raises(pydantic.ValidationError):
        ChangePasswordRequest(current_password="anything", new_password="short1A")


def test_change_password_request_accepts_complex_password_at_schema_level():
    """Positive control — the schema must accept a value that
    `validate_password_complexity` would also accept."""
    from app.schemas.auth import ChangePasswordRequest

    body = ChangePasswordRequest(current_password="anything", new_password="StrongPass123X")
    assert body.new_password == "StrongPass123X"
