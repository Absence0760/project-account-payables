"""Supplier-portal MFA (TOTP) tests.

Mirrors the employee MFA tests but for VendorUser + the `typ=vendor` JWT.
Covers: enroll → verify → login-now-challenges → challenge-verify → token;
disable; wrong-code rejected; and — the security-critical part — that the
three token types (`vendor` access, `vendor_mfa_challenge`, employee
`mfa_challenge`) stay strictly separate so there's no cross-auth leak.

Unit-level: the tenant DB session is mocked. `AP_MFA_ENABLED` is forced on for
the flow tests via the `settings` object (the master switch the endpoints read).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pyotp
import pytest
from fastapi import HTTPException

from app.api.deps import create_vendor_access_token
from app.api.portal_auth import (
    portal_login,
    portal_mfa_challenge,
    portal_mfa_disable,
    portal_mfa_enroll,
    portal_mfa_verify,
    portal_request_email_otp,
)
from app.config import settings
from app.schemas.portal import (
    PortalLoginRequest,
    PortalMFAChallengeResponse,
    PortalMFAChallengeVerifyRequest,
    PortalMFADisableRequest,
    PortalMFAEmailChallengeRequest,
    PortalMFAVerifyRequest,
    PortalTokenResponse,
)
from app.services import mfa


@pytest.fixture
def mfa_on(monkeypatch):
    """Flip the platform MFA master switch on for the duration of a test."""
    monkeypatch.setattr(settings, "mfa_enabled", True)
    yield


def _mock_db(*, vendor_user=None, vendor=None):
    """A MagicMock tenant session whose `execute(...).scalar_one_or_none()`
    returns `vendor_user` first then `vendor` (the order the endpoints query)."""
    returns = []
    if vendor_user is not None:
        returns.append(vendor_user)
    if vendor is not None:
        returns.append(vendor)

    seq = iter(returns)

    def _execute(*_a, **_k):
        res = MagicMock()
        try:
            res.scalar_one_or_none.return_value = next(seq)
        except StopIteration:
            res.scalar_one_or_none.return_value = None
        return res

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    return db


def _vendor_user(**overrides):
    base = dict(
        id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        email="supplier@vendor.com",
        full_name="Supplier Rep",
        hashed_password=None,
        is_active=True,
        must_change_password=False,
        mfa_secret=None,
        mfa_enabled=False,
        mfa_enrolled_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _vendor():
    return SimpleNamespace(id=uuid.uuid4(), name="Acme Supplies", status="active")


# ---------------------------------------------------------------------------
# Enroll → verify → flips mfa_enabled on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_then_verify_activates_mfa(mfa_on):
    vu = _vendor_user()
    db = _mock_db(vendor_user=None, vendor=_vendor())

    enroll = await portal_mfa_enroll(vu=vu, db=db)
    assert enroll.secret  # plaintext secret returned during enrollment
    assert enroll.provisioning_uri.startswith("otpauth://")
    assert enroll.qr_code_data_url.startswith("data:image/png;base64,")
    # Secret stored, but NOT yet active.
    assert vu.mfa_secret == enroll.secret
    assert vu.mfa_enabled is False

    code = pyotp.TOTP(vu.mfa_secret).now()
    me = await portal_mfa_verify(body=PortalMFAVerifyRequest(code=code), vu=vu, db=db)
    assert vu.mfa_enabled is True
    assert vu.mfa_enrolled_at is not None
    assert me.mfa_enabled is True


@pytest.mark.asyncio
async def test_verify_wrong_code_rejected(mfa_on):
    vu = _vendor_user(mfa_secret=pyotp.random_base32())
    db = _mock_db()
    with pytest.raises(HTTPException) as exc:
        await portal_mfa_verify(body=PortalMFAVerifyRequest(code="000000"), vu=vu, db=db)
    assert exc.value.status_code == 401
    assert vu.mfa_enabled is False


@pytest.mark.asyncio
async def test_verify_without_enrollment_400(mfa_on):
    vu = _vendor_user(mfa_secret=None)
    db = _mock_db()
    with pytest.raises(HTTPException) as exc:
        await portal_mfa_verify(body=PortalMFAVerifyRequest(code="123456"), vu=vu, db=db)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Login now challenges → challenge-verify → real token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_challenges_when_mfa_enrolled(mfa_on, monkeypatch):
    secret = pyotp.random_base32()
    vu = _vendor_user(hashed_password="x", mfa_secret=secret, mfa_enabled=True)
    db = _mock_db(vendor_user=vu)
    monkeypatch.setattr("app.api.portal_auth.pwd_context.verify", lambda *_: True)
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))

    res = await portal_login(
        body=PortalLoginRequest(email=vu.email, password="pw"),
        request=MagicMock(),
        db=db,
    )
    assert isinstance(res, PortalMFAChallengeResponse)
    assert res.mfa_required is True
    # TOTP primary + the email-OTP backup factor are both offered.
    assert res.methods == ["totp", "email"]
    # The challenge token must carry the vendor-challenge typ, NOT an access token.
    claims = await mfa.decode_vendor_challenge_token(res.mfa_challenge_token)
    assert claims.subject_id == vu.id


@pytest.mark.asyncio
async def test_login_no_challenge_when_not_enrolled(mfa_on, monkeypatch):
    vu = _vendor_user(hashed_password="x", mfa_enabled=False)
    db = _mock_db(vendor_user=vu)
    monkeypatch.setattr("app.api.portal_auth.pwd_context.verify", lambda *_: True)
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))

    res = await portal_login(
        body=PortalLoginRequest(email=vu.email, password="pw"),
        request=MagicMock(),
        db=db,
    )
    assert isinstance(res, PortalTokenResponse)
    assert res.access_token


@pytest.mark.asyncio
async def test_login_skips_mfa_when_master_switch_off(monkeypatch):
    """AP_MFA_ENABLED off → enrolled vendor still gets a plain token."""
    monkeypatch.setattr(settings, "mfa_enabled", False)
    vu = _vendor_user(hashed_password="x", mfa_secret=pyotp.random_base32(), mfa_enabled=True)
    db = _mock_db(vendor_user=vu)
    monkeypatch.setattr("app.api.portal_auth.pwd_context.verify", lambda *_: True)
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))

    res = await portal_login(
        body=PortalLoginRequest(email=vu.email, password="pw"),
        request=MagicMock(),
        db=db,
    )
    assert isinstance(res, PortalTokenResponse)


@pytest.mark.asyncio
async def test_challenge_verify_mints_access_token(mfa_on, monkeypatch):
    secret = pyotp.random_base32()
    vu = _vendor_user(mfa_secret=secret, mfa_enabled=True)
    db = _mock_db(vendor_user=vu)
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))

    challenge = mfa.create_vendor_challenge_token(vu.id)
    code = pyotp.TOTP(secret).now()
    res = await portal_mfa_challenge(
        body=PortalMFAChallengeVerifyRequest(challenge_token=challenge, code=code),
        request=MagicMock(),
        db=db,
    )
    assert isinstance(res, PortalTokenResponse)
    # The minted token is a real vendor access token.
    from app.api.deps import decode_token

    payload = decode_token(res.access_token)
    assert payload["typ"] == "vendor"
    assert payload["sub"] == str(vu.id)


@pytest.mark.asyncio
async def test_challenge_verify_wrong_code_rejected(mfa_on, monkeypatch):
    secret = pyotp.random_base32()
    vu = _vendor_user(mfa_secret=secret, mfa_enabled=True)
    db = _mock_db(vendor_user=vu)
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))

    challenge = mfa.create_vendor_challenge_token(vu.id)
    with pytest.raises(HTTPException) as exc:
        await portal_mfa_challenge(
            body=PortalMFAChallengeVerifyRequest(challenge_token=challenge, code="000000"),
            request=MagicMock(),
            db=db,
        )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Email-OTP backup factor — request a code, then verify it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_otp_request_sends_code_to_enrolled_vendor(mfa_on, monkeypatch):
    """An enrolled, active vendor with a valid challenge token gets a code
    issued + emailed to their account address."""
    secret = pyotp.random_base32()
    vu = _vendor_user(mfa_secret=secret, mfa_enabled=True)
    db = _mock_db(vendor_user=vu)
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))
    issue = AsyncMock(return_value="654321")
    monkeypatch.setattr("app.api.portal_auth.mfa.issue_vendor_email_otp", issue)
    send = AsyncMock()
    monkeypatch.setattr("app.api.portal_auth._send_vendor_email_otp", send)

    challenge = mfa.create_vendor_challenge_token(vu.id)
    res = await portal_request_email_otp(
        body=PortalMFAEmailChallengeRequest(challenge_token=challenge),
        request=MagicMock(),
        db=db,
    )
    assert res is None  # 204
    issue.assert_awaited_once_with(vu.id)
    # The code is handed to the email helper — never returned in the response.
    send.assert_awaited_once()
    assert send.await_args.args[1] == "654321"


@pytest.mark.asyncio
async def test_email_otp_request_silent_for_unenrolled_vendor(mfa_on, monkeypatch):
    """A vendor who hasn't enrolled MFA gets no code — and a silent 204 (no
    enumeration of which accounts exist / are enrolled)."""
    vu = _vendor_user(mfa_enabled=False)
    db = _mock_db(vendor_user=vu)
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))
    issue = AsyncMock()
    monkeypatch.setattr("app.api.portal_auth.mfa.issue_vendor_email_otp", issue)
    send = AsyncMock()
    monkeypatch.setattr("app.api.portal_auth._send_vendor_email_otp", send)

    challenge = mfa.create_vendor_challenge_token(vu.id)
    res = await portal_request_email_otp(
        body=PortalMFAEmailChallengeRequest(challenge_token=challenge),
        request=MagicMock(),
        db=db,
    )
    assert res is None
    issue.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_otp_request_rejects_employee_challenge(mfa_on, monkeypatch):
    """The email-request endpoint enforces the same vendor-challenge typ gate —
    an employee challenge token is a 401."""
    db = _mock_db()
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))
    employee_challenge = mfa.create_challenge_token(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await portal_request_email_otp(
            body=PortalMFAEmailChallengeRequest(challenge_token=employee_challenge),
            request=MagicMock(),
            db=db,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_challenge_verify_email_method_mints_token(mfa_on, monkeypatch):
    """method='email' + a valid email OTP mints a real vendor access token."""
    secret = pyotp.random_base32()
    vu = _vendor_user(mfa_secret=secret, mfa_enabled=True)
    db = _mock_db(vendor_user=vu)
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))
    verify = AsyncMock(return_value=True)
    monkeypatch.setattr("app.api.portal_auth.mfa.verify_vendor_email_otp", verify)

    challenge = mfa.create_vendor_challenge_token(vu.id)
    res = await portal_mfa_challenge(
        body=PortalMFAChallengeVerifyRequest(
            challenge_token=challenge, code="654321", method="email"
        ),
        request=MagicMock(),
        db=db,
    )
    assert isinstance(res, PortalTokenResponse)
    verify.assert_awaited_once_with(vu.id, "654321")
    from app.api.deps import decode_token

    assert decode_token(res.access_token)["typ"] == "vendor"


@pytest.mark.asyncio
async def test_challenge_verify_email_wrong_or_expired_code_rejected(mfa_on, monkeypatch):
    """A bad / expired email OTP is a 401 — and never falls through to TOTP."""
    secret = pyotp.random_base32()
    vu = _vendor_user(mfa_secret=secret, mfa_enabled=True)
    db = _mock_db(vendor_user=vu)
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.api.portal_auth.mfa.verify_vendor_email_otp", AsyncMock(return_value=False)
    )

    challenge = mfa.create_vendor_challenge_token(vu.id)
    with pytest.raises(HTTPException) as exc:
        await portal_mfa_challenge(
            body=PortalMFAChallengeVerifyRequest(
                challenge_token=challenge, code="000000", method="email"
            ),
            request=MagicMock(),
            db=db,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_email_otp_keyspace_isolated_from_employee(monkeypatch):
    """The vendor email-OTP Redis key is a DISTINCT prefix from the employee one,
    so the same UUID value can't collide across surfaces."""

    class _FakeRedis:
        def __init__(self):
            self.store = {}

        async def setex(self, key, ttl, val):
            self.store[key] = val

        async def get(self, key):
            return self.store.get(key)

        async def delete(self, key):
            self.store.pop(key, None)

    fake = _FakeRedis()
    monkeypatch.setattr("app.services.mfa.get_redis", AsyncMock(return_value=fake))

    same_id = uuid.uuid4()
    employee_code = await mfa.issue_email_otp(same_id)
    vendor_code = await mfa.issue_vendor_email_otp(same_id)
    keys = list(fake.store.keys())
    assert any(k.startswith(mfa.EMAIL_OTP_PREFIX) for k in keys)
    assert any(k.startswith(mfa.VENDOR_EMAIL_OTP_PREFIX) for k in keys)
    assert len(fake.store) == 2  # two separate slots, no overwrite

    # A vendor code must NOT verify against the employee keyspace and vice versa.
    assert await mfa.verify_vendor_email_otp(same_id, employee_code) is False
    assert await mfa.verify_email_otp(same_id, vendor_code) is False
    # Each verifies against its own slot, single-use.
    assert await mfa.verify_vendor_email_otp(same_id, vendor_code) is True
    assert await mfa.verify_vendor_email_otp(same_id, vendor_code) is False


# ---------------------------------------------------------------------------
# Disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_requires_valid_code(mfa_on):
    secret = pyotp.random_base32()
    vu = _vendor_user(mfa_secret=secret, mfa_enabled=True)
    db = _mock_db(vendor=_vendor())

    code = pyotp.TOTP(secret).now()
    me = await portal_mfa_disable(body=PortalMFADisableRequest(code=code), vu=vu, db=db)
    assert vu.mfa_enabled is False
    assert vu.mfa_secret is None
    assert me.mfa_enabled is False


@pytest.mark.asyncio
async def test_disable_wrong_code_rejected(mfa_on):
    secret = pyotp.random_base32()
    vu = _vendor_user(mfa_secret=secret, mfa_enabled=True)
    db = _mock_db()
    with pytest.raises(HTTPException) as exc:
        await portal_mfa_disable(body=PortalMFADisableRequest(code="000000"), vu=vu, db=db)
    assert exc.value.status_code == 401
    assert vu.mfa_enabled is True  # unchanged


# ---------------------------------------------------------------------------
# Token-type isolation — the cross-auth-leak guard
# ---------------------------------------------------------------------------


def test_vendor_challenge_token_distinct_typ():
    """The vendor challenge token must carry `vendor_mfa_challenge` — distinct
    from the employee `mfa_challenge` and the `vendor` access token."""
    vu_id = uuid.uuid4()
    from app.api.deps import decode_token

    tok = mfa.create_vendor_challenge_token(vu_id)
    assert decode_token(tok)["typ"] == "vendor_mfa_challenge"


@pytest.mark.asyncio
async def test_employee_challenge_token_not_accepted_as_vendor_challenge():
    """An employee MFA challenge token must NOT decode as a vendor challenge."""
    employee_challenge = mfa.create_challenge_token(uuid.uuid4())
    with pytest.raises(ValueError):
        await mfa.decode_vendor_challenge_token(employee_challenge)


@pytest.mark.asyncio
async def test_vendor_access_token_not_accepted_as_challenge():
    """A full vendor access token must NOT decode as a vendor MFA challenge."""
    access = create_vendor_access_token(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(ValueError):
        await mfa.decode_vendor_challenge_token(access)


@pytest.mark.asyncio
async def test_challenge_endpoint_rejects_employee_challenge_token(mfa_on, monkeypatch):
    """Hitting the portal challenge endpoint with an EMPLOYEE challenge token
    is a 401 — the typ check blocks the cross-surface confusion."""
    db = _mock_db()
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))
    employee_challenge = mfa.create_challenge_token(uuid.uuid4())
    code = "123456"
    with pytest.raises(HTTPException) as exc:
        await portal_mfa_challenge(
            body=PortalMFAChallengeVerifyRequest(challenge_token=employee_challenge, code=code),
            request=MagicMock(),
            db=db,
        )
    assert exc.value.status_code == 401
