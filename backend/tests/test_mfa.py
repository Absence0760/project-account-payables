"""Unit tests for the MFA service surface.

Covers TOTP secret/URI/QR generation + verification, email-OTP issue/verify,
challenge-token round-trip, and org-enforcement reads. Live login + endpoint
flows would need a Postgres + Redis harness, which the rest of this repo
exercises elsewhere.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pyotp
import pytest

# ---------- TOTP ---------------------------------------------------------


def test_generate_totp_secret_is_base32_and_unique():
    from app.services import mfa

    a, b = mfa.generate_totp_secret(), mfa.generate_totp_secret()
    assert a != b
    # base32 alphabet (uppercase + 2-7), length 32 per pyotp default
    assert len(a) == 32
    assert all(ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for ch in a)


def test_provisioning_uri_includes_issuer_and_account():
    from app.services import mfa

    uri = mfa.provisioning_uri("JBSWY3DPEHPK3PXP", "user@acme.com", issuer="Acme AP")
    assert uri.startswith("otpauth://totp/")
    assert "Acme%20AP" in uri
    assert "user%40acme.com" in uri
    assert "secret=JBSWY3DPEHPK3PXP" in uri


def test_qr_code_data_url_is_png_data_uri():
    from app.services import mfa

    uri = mfa.provisioning_uri("JBSWY3DPEHPK3PXP", "user@acme.com")
    data_url = mfa.qr_code_data_url(uri)
    assert data_url.startswith("data:image/png;base64,")
    # PNG bodies aren't tiny; sanity-check minimum size
    assert len(data_url) > 200


def test_verify_totp_accepts_current_code():
    from app.services import mfa

    secret = mfa.generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert mfa.verify_totp(secret, code) is True


def test_verify_totp_rejects_bad_code():
    from app.services import mfa

    secret = mfa.generate_totp_secret()
    assert mfa.verify_totp(secret, "000000") is False
    assert mfa.verify_totp(secret, "") is False
    assert mfa.verify_totp("", "123456") is False


def test_verify_totp_strips_whitespace():
    from app.services import mfa

    secret = mfa.generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert mfa.verify_totp(secret, f"  {code}  ") is True


# ---------- Org enforcement ---------------------------------------------


def test_org_requires_mfa_false_when_master_switch_off():
    from app.services import mfa

    with patch("app.services.mfa.settings") as fake:
        fake.mfa_enabled = False
        assert mfa.org_requires_mfa({"mfa": {"required": True}}) is False


def test_org_requires_mfa_reads_settings():
    from app.services import mfa

    with patch("app.services.mfa.settings") as fake:
        fake.mfa_enabled = True
        assert mfa.org_requires_mfa(None) is False
        assert mfa.org_requires_mfa({}) is False
        assert mfa.org_requires_mfa({"mfa": {}}) is False
        assert mfa.org_requires_mfa({"mfa": {"required": False}}) is False
        assert mfa.org_requires_mfa({"mfa": {"required": True}}) is True


# ---------- Email OTP ----------------------------------------------------


class _FakeRedis:
    """Minimal in-memory stand-in that satisfies setex/get/delete."""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def setex(self, key, ttl, value):  # noqa: ARG002 — TTL ignored in tests
        self.store[key] = value if isinstance(value, bytes) else value.encode("utf-8")

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.services.mfa.get_redis", _get_redis)
    return fake


def test_issue_email_otp_returns_six_digits(fake_redis):
    from app.services import mfa

    user_id = uuid.uuid4()
    code = asyncio.run(mfa.issue_email_otp(user_id))
    assert len(code) == 6
    assert code.isdigit()
    # Stored as a hash, never plaintext
    stored = fake_redis.store[f"mfa:email_otp:{user_id}"].decode()
    assert stored != code
    assert len(stored) == 64  # sha256 hex


def test_verify_email_otp_consumes_code(fake_redis):  # noqa: ARG001 — fixture activation
    from app.services import mfa

    user_id = uuid.uuid4()
    code = asyncio.run(mfa.issue_email_otp(user_id))
    assert asyncio.run(mfa.verify_email_otp(user_id, code)) is True
    # Single-use — second attempt fails
    assert asyncio.run(mfa.verify_email_otp(user_id, code)) is False


def test_verify_email_otp_rejects_wrong_code(fake_redis):  # noqa: ARG001
    from app.services import mfa

    user_id = uuid.uuid4()
    asyncio.run(mfa.issue_email_otp(user_id))
    assert asyncio.run(mfa.verify_email_otp(user_id, "999999")) is False


def test_verify_email_otp_returns_false_when_no_code_issued(fake_redis):  # noqa: ARG001
    from app.services import mfa

    assert asyncio.run(mfa.verify_email_otp(uuid.uuid4(), "123456")) is False


# ---------- Challenge token round-trip ----------------------------------


def test_challenge_token_round_trip():
    from app.services import mfa

    user_id = uuid.uuid4()
    token = mfa.create_challenge_token(user_id)
    assert isinstance(token, str)
    assert mfa.decode_challenge_token(token) == user_id


def test_challenge_token_rejects_garbage():
    from app.services import mfa

    with pytest.raises(ValueError):
        mfa.decode_challenge_token("not-a-jwt")


def test_challenge_token_rejects_wrong_type():
    """A regular access token must not satisfy the challenge check."""
    from app.api.deps import create_access_token
    from app.services import mfa

    bad = create_access_token(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(ValueError):
        mfa.decode_challenge_token(bad)


# ---------- Schema contract ---------------------------------------------


def test_mfa_challenge_response_shape():
    """Frontend depends on these fields being on the body when MFA is required."""
    from app.schemas.auth import MFAChallengeResponse

    resp = MFAChallengeResponse(
        mfa_challenge_token="t",
        methods=["totp", "email"],
        must_enroll=False,
    )
    data = resp.model_dump()
    assert data["mfa_required"] is True
    assert data["mfa_challenge_token"] == "t"
    assert data["methods"] == ["totp", "email"]
    assert data["must_enroll"] is False


def test_user_response_includes_mfa_fields():
    from app.schemas.auth import UserResponse

    resp = UserResponse(
        id="u",
        email="a@b.c",
        full_name="A",
        organization_id="o",
        is_active=True,
        mfa_enabled=True,
        mfa_required_by_org=True,
        roles=[],
    )
    data = resp.model_dump()
    assert data["mfa_enabled"] is True
    assert data["mfa_required_by_org"] is True


# ---------- Audit-log coverage for MFA paths ----------------------------
#
# SOC 2 wants every auth event (including MFA success + failure) to land
# in the audit trail. These tests mock out dispatch_auth_audit and assert
# the expected action names get written — the full integration tests live
# in test_auth_events.py alongside the login/logout paths.


@pytest.mark.asyncio
async def test_verify_mfa_success_writes_audit_actions():
    from unittest.mock import patch as _patch

    import pyotp

    from app.api import auth as auth_mod
    from app.schemas.auth import MFAVerifyRequest
    from app.services import mfa as mfa_svc

    user = _mfa_user(enrolled=True)
    db = _single_user_db(user)

    audit_calls: list[str] = []

    async def _fake_audit(**kwargs):
        audit_calls.append(kwargs["action"])

    async def _fake_register(user_id, jti):
        pass

    challenge = mfa_svc.create_challenge_token(user.id)
    code = pyotp.TOTP(user.mfa_secret).now()

    with (
        _patch.object(auth_mod, "dispatch_auth_audit", _fake_audit),
        _patch.object(auth_mod, "register_session", _fake_register),
        _patch.object(auth_mod.settings, "mfa_enabled", True),
    ):
        await auth_mod.verify_mfa(
            MFAVerifyRequest(challenge_token=challenge, code=code, method="totp"),
            _mfa_request(),
            db,
        )

    assert "auth.mfa.verify.success" in audit_calls
    assert "auth.login.success" in audit_calls


@pytest.mark.asyncio
async def test_verify_mfa_failure_writes_audit_action():
    from unittest.mock import patch as _patch

    import pyotp
    from fastapi import HTTPException

    from app.api import auth as auth_mod
    from app.schemas.auth import MFAVerifyRequest
    from app.services import mfa as mfa_svc

    user = _mfa_user(enrolled=True)
    user.mfa_secret = pyotp.random_base32()
    db = _single_user_db(user)

    audit_calls: list[str] = []

    async def _fake_audit(**kwargs):
        audit_calls.append(kwargs["action"])

    challenge = mfa_svc.create_challenge_token(user.id)

    with (
        _patch.object(auth_mod, "dispatch_auth_audit", _fake_audit),
        _patch.object(auth_mod.settings, "mfa_enabled", True),
    ):
        with pytest.raises(HTTPException):
            await auth_mod.verify_mfa(
                MFAVerifyRequest(challenge_token=challenge, code="000000", method="totp"),
                _mfa_request(),
                db,
            )

    assert "auth.mfa.verify.failure" in audit_calls
    assert "auth.mfa.verify.success" not in audit_calls
    assert "auth.login.success" not in audit_calls


# --- tiny local helpers for the two tests above ----------------------


def _mfa_user(*, enrolled: bool):
    from types import SimpleNamespace

    import pyotp

    return SimpleNamespace(
        id=uuid.uuid4(),
        email="a@b.c",
        full_name="A",
        hashed_password=None,
        organization_id=uuid.uuid4(),
        is_active=True,
        must_change_password=False,
        mfa_enabled=enrolled,
        mfa_secret=pyotp.random_base32() if enrolled else None,
    )


def _single_user_db(user):
    from unittest.mock import AsyncMock, MagicMock

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=user)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


def _mfa_request():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    req = MagicMock()
    req.client = SimpleNamespace(host="127.0.0.1")
    return req
