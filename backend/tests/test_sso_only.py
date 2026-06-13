"""SSO-only mode — password login is closed when a tenant requires SSO.

`is_sso_only` is deterministic (unit-tested below); the login enforcement is
exercised by calling the `login` handler directly with mocked DB sessions, the
same DB-free pattern as test_auth_error_consistency.py.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.sso import is_sso_only


@pytest.mark.parametrize(
    "settings_dict,expected",
    [
        (None, False),
        ({}, False),
        ({"sso": {}}, False),
        ({"sso": {"sso_only": True}}, False),  # enabled missing => no lockout
        ({"sso": {"enabled": True}}, False),  # sso_only not set
        ({"sso": {"enabled": False, "sso_only": True}}, False),  # SSO off
        ({"sso": {"enabled": True, "sso_only": True}}, True),
    ],
)
def test_is_sso_only(settings_dict, expected):
    assert is_sso_only(settings_dict) is expected


def _fake_request(ip: str = "203.0.113.1"):
    req = MagicMock()
    req.client = SimpleNamespace(host=ip)
    req.headers = {}
    return req


def _db_user_then_org(user, org):
    """Login does two queries: the user lookup, then the org load. Model both."""
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)
    org_result = MagicMock()
    org_result.scalar_one_or_none = MagicMock(return_value=org)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[user_result, org_result, org_result, org_result])
    return db


def _user_with_password(pw: str):
    from app.utils.passwords import pwd_context

    return SimpleNamespace(
        id=uuid.uuid4(),
        email="u@acme.com",
        organization_id=uuid.uuid4(),
        is_active=True,
        mfa_enabled=False,
        hashed_password=pwd_context.hash(pw),
        must_change_password=False,
        full_name="U",
    )


@pytest.mark.asyncio
async def test_login_rejected_when_sso_only():
    """A correct password in an sso_only org is refused with 403 + audit —
    password users can't bypass SSO."""
    from app.api.auth import login
    from app.schemas.auth import LoginRequest

    pw = "Correct-Horse-9"
    user = _user_with_password(pw)
    org = SimpleNamespace(
        id=user.organization_id,
        settings={"sso": {"enabled": True, "sso_only": True, "provider": "saml"}},
    )

    audits: list[dict] = []

    async def _audit(**kwargs):
        audits.append(kwargs)

    with patch("app.api.auth.dispatch_auth_audit", _audit):
        with pytest.raises(HTTPException) as exc:
            await login(
                body=LoginRequest(email="u@acme.com", password=pw),
                request=_fake_request(),
                db=_db_user_then_org(user, org),
            )

    assert exc.value.status_code == 403
    assert "single sign-on" in exc.value.detail.lower()
    assert any(a["details"].get("reason") == "sso_only" for a in audits)


@pytest.mark.asyncio
async def test_login_allowed_when_not_sso_only():
    """The same correct password succeeds when the org doesn't require SSO —
    proving the gate is the flag, not a blanket block."""
    from app.api.auth import login
    from app.schemas.auth import LoginRequest

    pw = "Correct-Horse-9"
    user = _user_with_password(pw)
    org = SimpleNamespace(
        id=user.organization_id,
        settings={"sso": {"enabled": True, "provider": "saml"}},  # sso_only absent
    )

    with (
        patch("app.api.auth.dispatch_auth_audit", AsyncMock()),
        patch("app.api.auth.register_session", AsyncMock()),
    ):
        result = await login(
            body=LoginRequest(email="u@acme.com", password=pw),
            request=_fake_request(),
            db=_db_user_then_org(user, org),
        )

    # Not a 403 — a real token response.
    assert getattr(result, "access_token", None)
