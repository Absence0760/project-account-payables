"""MFA enrollment security — the second-factor lifecycle.

`test_mfa.py` covers the helper mechanics; `test_mfa_security.py`
covers replay / single-use of codes. This file pins the *enrollment*
contract:

  - Master switch off ⇒ enroll / verify / disable all 400
  - Fresh enrollment mints a new secret; the previous one is invalidated
  - `mfa_enabled` is False until verify succeeds (the pending window
    is a partial state, not a live second factor)
  - Verify with the wrong code does NOT flip mfa_enabled
  - Disable requires password re-entry (stolen session can't strip MFA)
  - Disable is refused when the org enforces MFA — a regression that
    let users opt out under org-required would silently downgrade the
    tenant's security posture
  - The MFAEnrollStartResponse exposes the secret EXACTLY once (only
    on the enrollment-start path, never on /me or /mfa/disable)
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _fake_user(
    *, has_password: bool = True, mfa_secret: str | None = None, mfa_enabled: bool = False
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="user@acme.test",
        full_name="Test User",
        organization_id=uuid.uuid4(),
        is_active=True,
        hashed_password=(
            "$bcrypt-sha256$v=2,t=2b,r=12$Jl.B.u9pD6kCuDNdO0nFfu$cK3Jg2DYkqzysEPJ0Q1opQpBVRQtyka"
        )
        if has_password
        else None,
        mfa_secret=mfa_secret,
        mfa_enabled=mfa_enabled,
        mfa_enrolled_at=None,
        must_change_password=False,
        delegate_to_id=None,
        delegate_until=None,
        roles=[],  # _user_response reads this; empty list is fine
    )


def _db_returning_org(org):
    """Mock the control session for an org lookup inside disable_mfa."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=org)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Master switch — enroll / verify / disable refused when MFA disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_mfa_start_refused_when_master_switch_off():
    from app.api.auth import enroll_mfa_start

    user = _fake_user()
    db = AsyncMock()
    with patch("app.api.auth.settings.mfa_enabled", False):
        with pytest.raises(HTTPException) as exc:
            await enroll_mfa_start(user=user, db=db)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_enroll_mfa_verify_refused_when_master_switch_off():
    from app.api.auth import enroll_mfa_verify
    from app.schemas.auth import MFAEnrollVerifyRequest

    user = _fake_user(mfa_secret="ABCDEFGH")
    db = AsyncMock()
    with patch("app.api.auth.settings.mfa_enabled", False):
        with pytest.raises(HTTPException) as exc:
            await enroll_mfa_verify(body=MFAEnrollVerifyRequest(code="123456"), user=user, db=db)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Enroll flow — secret freshness + verify required to flip on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_mfa_start_mints_a_new_secret_and_disables_until_verified():
    """A fresh enroll request must overwrite any prior secret and
    explicitly mark mfa_enabled=False — until verify, the user has
    NO second factor in effect."""
    from app.api.auth import enroll_mfa_start

    # User had a prior enrolled state (mfa_enabled=True with a secret).
    user = _fake_user(mfa_secret="OLD-SECRET-ABCD", mfa_enabled=True)
    db = AsyncMock()
    db.commit = AsyncMock()

    with patch("app.api.auth.settings.mfa_enabled", True):
        resp = await enroll_mfa_start(user=user, db=db)

    # Old secret rotated.
    assert user.mfa_secret != "OLD-SECRET-ABCD"
    assert user.mfa_secret == resp.secret
    # Until verify completes, MFA is OFF — the partial state must not
    # be treated as a live second factor.
    assert user.mfa_enabled is False
    assert user.mfa_enrolled_at is None


@pytest.mark.asyncio
async def test_enroll_mfa_verify_with_wrong_code_keeps_mfa_disabled():
    """A wrong code at verify must NOT flip mfa_enabled. A regression
    that did so would let an attacker who started enrollment land
    "enrolled" without ever proving they hold the secret."""
    from app.api.auth import enroll_mfa_verify
    from app.schemas.auth import MFAEnrollVerifyRequest

    user = _fake_user(mfa_secret="JBSWY3DPEHPK3PXP", mfa_enabled=False)
    db = AsyncMock()
    db.commit = AsyncMock()

    with (
        patch("app.api.auth.settings.mfa_enabled", True),
        patch("app.api.auth.mfa.verify_totp", return_value=False),
    ):
        with pytest.raises(HTTPException) as exc:
            await enroll_mfa_verify(
                body=MFAEnrollVerifyRequest(code="000000"),
                user=user,
                db=db,
            )

    assert exc.value.status_code == 401
    assert user.mfa_enabled is False, "wrong-code verify must not flip mfa_enabled"
    assert user.mfa_enrolled_at is None
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_enroll_mfa_verify_refused_when_no_secret_present():
    """Calling verify before start (no secret in flight) must 400,
    not silently flip mfa_enabled to True on an empty secret. The
    secret IS the credential — verifying nothing must not pass."""
    from app.api.auth import enroll_mfa_verify
    from app.schemas.auth import MFAEnrollVerifyRequest

    user = _fake_user(mfa_secret=None, mfa_enabled=False)
    db = AsyncMock()

    with patch("app.api.auth.settings.mfa_enabled", True):
        with pytest.raises(HTTPException) as exc:
            await enroll_mfa_verify(
                body=MFAEnrollVerifyRequest(code="123456"),
                user=user,
                db=db,
            )

    assert exc.value.status_code == 400
    assert user.mfa_enabled is False


@pytest.mark.asyncio
async def test_enroll_mfa_verify_with_correct_code_flips_on():
    """Positive control — without it, the negative tests could pass
    because verify always rejects."""
    from app.api.auth import enroll_mfa_verify
    from app.schemas.auth import MFAEnrollVerifyRequest

    org = SimpleNamespace(
        id=uuid.uuid4(),
        slug="acme",
        name="Acme",
        plan="pro",
        settings={},
    )
    user = _fake_user(mfa_secret="JBSWY3DPEHPK3PXP", mfa_enabled=False)

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=org)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    with (
        patch("app.api.auth.settings.mfa_enabled", True),
        patch("app.api.auth.mfa.verify_totp", return_value=True),
    ):
        resp = await enroll_mfa_verify(
            body=MFAEnrollVerifyRequest(code="999999"),
            user=user,
            db=db,
        )

    assert user.mfa_enabled is True
    assert user.mfa_enrolled_at is not None
    assert resp.mfa_enabled is True
    db.commit.assert_called()


# ---------------------------------------------------------------------------
# Disable — password re-entry + org-required check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_mfa_requires_password_re_entry():
    """A stolen session must not strip MFA. The disable handler
    re-verifies the password as a soft re-auth — without that,
    anyone with the bearer token can rip out the second factor."""
    from app.api.auth import disable_mfa
    from app.schemas.auth import MFADisableRequest

    user = _fake_user(mfa_secret="JBSWY3DPEHPK3PXP", mfa_enabled=True)
    db = AsyncMock()

    with patch("app.api.auth.pwd_context.verify", return_value=False):
        with pytest.raises(HTTPException) as exc:
            await disable_mfa(body=MFADisableRequest(password="guess"), user=user, db=db)
    assert exc.value.status_code == 400
    # State untouched.
    assert user.mfa_enabled is True
    assert user.mfa_secret == "JBSWY3DPEHPK3PXP"


@pytest.mark.asyncio
async def test_disable_mfa_refused_when_org_requires_mfa():
    """When the org has flipped on `mfa.required`, individual users
    can't opt out. A regression that let them would silently downgrade
    the whole tenant's security posture."""
    from app.api.auth import disable_mfa
    from app.schemas.auth import MFADisableRequest

    org = SimpleNamespace(
        id=uuid.uuid4(),
        slug="acme",
        settings={"mfa": {"required": True}},
    )
    user = _fake_user(mfa_secret="JBSWY3DPEHPK3PXP", mfa_enabled=True)
    user.organization_id = org.id

    db = _db_returning_org(org)

    with (
        patch("app.api.auth.pwd_context.verify", return_value=True),
        patch("app.api.auth.settings.mfa_enabled", True),
        patch("app.api.auth.mfa.org_requires_mfa", return_value=True),
    ):
        with pytest.raises(HTTPException) as exc:
            await disable_mfa(body=MFADisableRequest(password="correct"), user=user, db=db)
    assert exc.value.status_code == 400
    assert "organization requires MFA" in exc.value.detail
    # Disable rejected → state intact.
    assert user.mfa_enabled is True


@pytest.mark.asyncio
async def test_disable_mfa_clears_secret_and_enrolled_at_on_success():
    """When disable succeeds (right password, org doesn't enforce),
    the secret must be wiped — keeping a stale secret around would
    let a re-enable shortcut around the enrollment ceremony."""
    from app.api.auth import disable_mfa
    from app.schemas.auth import MFADisableRequest

    org = SimpleNamespace(
        id=uuid.uuid4(),
        slug="acme",
        name="Acme",
        plan="pro",
        settings={},
    )
    user = _fake_user(mfa_secret="JBSWY3DPEHPK3PXP", mfa_enabled=True)
    user.organization_id = org.id
    db = _db_returning_org(org)

    with (
        patch("app.api.auth.pwd_context.verify", return_value=True),
        patch("app.api.auth.mfa.org_requires_mfa", return_value=False),
    ):
        await disable_mfa(
            body=MFADisableRequest(password="correct"),
            user=user,
            db=db,
        )

    assert user.mfa_enabled is False
    assert user.mfa_secret is None
    assert user.mfa_enrolled_at is None


# ---------------------------------------------------------------------------
# Secret exposure — only on the enroll-start response
# ---------------------------------------------------------------------------


def test_user_response_schema_does_not_expose_mfa_secret():
    """`/api/auth/me` and other endpoints that surface the User
    object must NOT include the TOTP secret. The MFAEnrollStartResponse
    is the only response shape that legitimately carries the secret."""
    from app.schemas.auth import UserResponse

    fields = set(UserResponse.model_fields.keys())
    assert "mfa_secret" not in fields
    assert "secret" not in fields


def test_only_enroll_start_response_carries_the_secret():
    """Sweep the auth schemas — only `MFAEnrollStartResponse` may
    declare `secret`. A future "let me show the secret on /me" PR
    would be caught here."""
    import app.schemas.auth as auth_schemas

    permitted = {"MFAEnrollStartResponse"}
    violations: list[str] = []

    for name, cls in inspect.getmembers(auth_schemas, inspect.isclass):
        if not hasattr(cls, "model_fields"):
            continue
        for fname in cls.model_fields:
            if "secret" in fname.lower() and name not in permitted:
                violations.append(f"{name}.{fname}")

    assert not violations, (
        f"schema(s) exposing a `secret` field outside enrollment-start: {violations}"
    )


def test_user_response_carries_mfa_enabled_flag_not_secret():
    """The UI needs to know whether MFA is on — but only as a
    boolean. Confirm the bool is present (so the UI can render the
    enroll / disable buttons correctly)."""
    from app.schemas.auth import UserResponse

    assert "mfa_enabled" in UserResponse.model_fields
