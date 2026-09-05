"""A deactivated account must not be able to complete a sign-in — on any path.

`users.is_active = false` is how offboarding is expressed: an admin flips it via
`PATCH /api/admin/users/{id}`, or the IdP deprovisions through SCIM
(`active: false` / `DELETE /scim/v2/Users/{id}`).

`get_current_user` has always refused an inactive user, so a token minted for
one was inert. But three sign-in entry points did not check at all — password
login, the OIDC callback, and the SAML ACS — so each happily returned HTTP 200
with an access token. The consequences were real even though no privilege
leaked: the SPA stored the token and considered itself signed in before 401-ing
on every subsequent call, the session was tracked against
`FEOH_MAX_CONCURRENT_SESSIONS`, and the SOX trail recorded an
`auth.login.success` / `auth.sso.login.success` for someone who had been
terminated. `/auth/mfa/verify`, the passkey verify, and the whole supplier
portal already refused; these tests pin the other three to the same rule.

The password-login case is exercised directly against the handler (no DB
needed); the SSO cases go through `jit_provision`, the shared chokepoint both
protocols funnel into, against real Postgres.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.models.organization import Organization
from app.models.user import User
from app.services.identity_provisioning import DeactivatedAccount, jit_provision
from app.utils.passwords import pwd_context

# ---------------------------------------------------------------------------
# Password login
# ---------------------------------------------------------------------------

_PASSWORD = "CorrectHorse123"


def _fake_request(ip: str = "10.0.0.1"):
    req = MagicMock()
    req.client = SimpleNamespace(host=ip)
    req.headers = {}
    return req


def _user(*, is_active: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="offboarded@acme.test",
        hashed_password=pwd_context.hash(_PASSWORD),
        organization_id=uuid.uuid4(),
        is_active=is_active,
        must_change_password=False,
        mfa_enabled=False,
        mfa_secret=None,
        full_name="Offboarded Employee",
    )


def _db_for(user) -> AsyncMock:
    """AsyncMock control session: first execute() yields the user, second the org."""
    db = AsyncMock()
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)
    org = SimpleNamespace(id=uuid.uuid4(), settings={})
    org_result = MagicMock()
    org_result.scalar_one_or_none = MagicMock(return_value=org)
    db.execute = AsyncMock(side_effect=[user_result, org_result])
    db.commit = AsyncMock()
    return db


async def _call_login(user):
    from app.api import auth as auth_mod
    from app.schemas.auth import LoginRequest

    audits: list[dict] = []
    sessions: list[tuple] = []

    async def _audit(**kwargs):
        audits.append(kwargs)

    def _queue_audit(**kwargs):
        # A login FAILURE row is queued off the response path rather than
        # awaited, so that a known address costs no more than an unknown one
        # (`tests/test_login_audit_off_response_path.py`). Same payload.
        audits.append(kwargs)

    async def _register(user_id, jti, **kwargs):
        sessions.append((user_id, jti))

    with (
        patch.object(auth_mod, "dispatch_auth_audit", _audit),
        patch.object(auth_mod, "queue_auth_audit", _queue_audit),
        patch.object(auth_mod, "register_session", _register),
        patch.object(auth_mod.settings, "mfa_enabled", False),
    ):
        try:
            resp = await auth_mod.login(
                LoginRequest(email=user.email, password=_PASSWORD),
                _fake_request(),
                _db_for(user),
            )
        except HTTPException as exc:
            return None, exc, audits, sessions
    return resp, None, audits, sessions


@pytest.mark.asyncio
async def test_password_login_refuses_a_deactivated_account():
    """The right password on a deactivated account is still a 401 — and no
    token, no tracked session, no `auth.login.success` row."""
    user = _user(is_active=False)
    resp, exc, audits, sessions = await _call_login(user)

    assert resp is None
    assert exc is not None and exc.status_code == 401
    # Same opaque detail as an unknown address / wrong password: a deactivated
    # account must not become an account-status oracle.
    assert exc.detail == "Invalid credentials"
    assert sessions == []
    actions = [a["action"] for a in audits]
    assert "auth.login.success" not in actions
    assert actions == ["auth.login.failure"]
    assert audits[0]["details"]["reason"] == "inactive"


@pytest.mark.asyncio
async def test_password_login_still_succeeds_for_an_active_account():
    """Guard the other direction — the check must not break normal sign-in."""
    user = _user(is_active=True)
    resp, exc, audits, sessions = await _call_login(user)

    assert exc is None
    assert resp is not None and resp.access_token
    assert len(sessions) == 1
    assert "auth.login.success" in [a["action"] for a in audits]


# ---------------------------------------------------------------------------
# SSO / SAML — both protocols funnel through `jit_provision`
# ---------------------------------------------------------------------------


async def _make_user(mk, org_id, *, is_active: bool, sso_id: str | None) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            User(
                id=user_id,
                email=f"jit-probe-{user_id}@example.test",
                full_name="JIT Probe",
                hashed_password=None,
                sso_provider="okta" if sso_id else None,
                sso_provider_id=sso_id,
                organization_id=org_id,
                is_active=is_active,
                must_change_password=False,
            )
        )
        await s.commit()
    return user_id


async def _drop_user(mk, user_id: uuid.UUID) -> None:
    async with mk() as s:
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


async def _org(mk, org_id: uuid.UUID) -> Organization:
    async with mk() as s:
        return (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()


@pytest.mark.asyncio
async def test_jit_provision_refuses_a_deactivated_user_matched_by_sso_id(realdb):
    """Branch 1 (durable `(sso_provider, sso_provider_id)` match)."""
    org_id = realdb.info("a").org_id
    mk = realdb.control_sessionmaker()
    sub = f"okta|{uuid.uuid4()}"
    user_id = await _make_user(mk, org_id, is_active=False, sso_id=sub)
    org = await _org(mk, org_id)
    try:
        async with mk() as s:
            with pytest.raises(DeactivatedAccount) as exc:
                await jit_provision(s, org, "whatever@example.test", sub, "okta", {})
        assert exc.value.user_id == user_id
    finally:
        await _drop_user(mk, user_id)


@pytest.mark.asyncio
async def test_jit_provision_refuses_a_deactivated_user_matched_by_email(realdb):
    """Branch 2 (email link) — and the SSO identifiers must NOT be rebound.

    Refusing after the re-link would let a login attempt against a disabled
    account silently repoint `sso_provider_id` at whatever subject the IdP
    presented.
    """
    org_id = realdb.info("a").org_id
    mk = realdb.control_sessionmaker()
    user_id = await _make_user(mk, org_id, is_active=False, sso_id=None)
    org = await _org(mk, org_id)
    try:
        async with mk() as s:
            email = (await s.get(User, user_id)).email
            with pytest.raises(DeactivatedAccount) as exc:
                await jit_provision(s, org, email, "saml|attacker-subject", "saml", {})
        assert exc.value.user_id == user_id

        async with mk() as s:
            row = await s.get(User, user_id)
            assert row.sso_provider is None
            assert row.sso_provider_id is None
    finally:
        await _drop_user(mk, user_id)


@pytest.mark.asyncio
async def test_jit_provision_still_returns_an_active_user(realdb):
    """Guard the other direction — an active account still signs in via SSO."""
    org_id = realdb.info("a").org_id
    mk = realdb.control_sessionmaker()
    sub = f"okta|{uuid.uuid4()}"
    user_id = await _make_user(mk, org_id, is_active=True, sso_id=sub)
    org = await _org(mk, org_id)
    try:
        async with mk() as s:
            user = await jit_provision(s, org, "whatever@example.test", sub, "okta", {})
        assert user.id == user_id
    finally:
        await _drop_user(mk, user_id)
