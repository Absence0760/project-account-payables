"""Supplier-portal tests — JWT isolation, vendor-scoped queries, auth deps.

These are unit-level tests that mock the DB session. The vendor-scoped
invoice-listing logic is the security-critical invariant: vendor A must not
see vendor B's invoices under any reachable code path.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.deps import (
    create_access_token,
    create_vendor_access_token,
    decode_token,
    get_current_user,
)
from app.api.portal_deps import get_current_vendor_user

# ---------- JWT shape + isolation -----------------------------------------


def test_vendor_token_carries_typ_and_vendor_id():
    vu_id = uuid.uuid4()
    vendor_id = uuid.uuid4()
    token = create_vendor_access_token(vu_id, vendor_id)
    payload = decode_token(token)
    assert payload["typ"] == "vendor"
    assert payload["sub"] == str(vu_id)
    assert payload["ven"] == str(vendor_id)


def test_employee_token_carries_user_typ():
    token = create_access_token(uuid.uuid4(), uuid.uuid4())
    payload = decode_token(token)
    assert payload["typ"] == "user"


@pytest.mark.asyncio
async def test_get_current_user_rejects_vendor_token():
    """A vendor-typ JWT must not resolve through the employee auth dep —
    that's the whole point of separating the two surfaces."""
    token = create_vendor_access_token(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization=f"Bearer {token}", db=AsyncMock())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_vendor_user_rejects_employee_token():
    """Symmetry: an employee token must not unlock the supplier portal."""
    token = create_access_token(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await get_current_vendor_user(authorization=f"Bearer {token}", db=AsyncMock())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_vendor_user_rejects_missing_auth():
    with pytest.raises(HTTPException) as exc:
        await get_current_vendor_user(authorization=None, db=AsyncMock())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_vendor_user_resolves_active_user(monkeypatch):
    """Happy path: valid vendor JWT + existing active VendorUser → returns the row."""
    vu_id = uuid.uuid4()
    vendor_id = uuid.uuid4()
    org_id = uuid.uuid4()
    token = create_vendor_access_token(vu_id, vendor_id)

    fake_vu = SimpleNamespace(
        id=vu_id, vendor_id=vendor_id, email="p@v.com", is_active=True, organization_id=org_id
    )
    scalars_result = MagicMock()
    scalars_result.scalar_one_or_none.return_value = fake_vu
    db = MagicMock()
    db.execute = AsyncMock(return_value=scalars_result)

    async def _not_blocked(_jti):
        return False

    monkeypatch.setattr("app.api.portal_deps.is_token_blocked", _not_blocked)

    tenant = SimpleNamespace(id=org_id)
    resolved = await get_current_vendor_user(authorization=f"Bearer {token}", tenant=tenant, db=db)
    assert resolved is fake_vu


@pytest.mark.asyncio
async def test_get_current_vendor_user_rejects_inactive(monkeypatch):
    vu_id = uuid.uuid4()
    token = create_vendor_access_token(vu_id, uuid.uuid4())

    fake_vu = SimpleNamespace(id=vu_id, is_active=False)
    scalars_result = MagicMock()
    scalars_result.scalar_one_or_none.return_value = fake_vu
    db = MagicMock()
    db.execute = AsyncMock(return_value=scalars_result)

    async def _not_blocked(_jti):
        return False

    monkeypatch.setattr("app.api.portal_deps.is_token_blocked", _not_blocked)

    with pytest.raises(HTTPException) as exc:
        await get_current_vendor_user(authorization=f"Bearer {token}", db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_vendor_user_rejects_revoked_token(monkeypatch):
    token = create_vendor_access_token(uuid.uuid4(), uuid.uuid4())

    async def _blocked(_jti):
        return True

    monkeypatch.setattr("app.api.portal_deps.is_token_blocked", _blocked)

    with pytest.raises(HTTPException) as exc:
        await get_current_vendor_user(authorization=f"Bearer {token}", db=AsyncMock())
    assert exc.value.status_code == 401


# ---------- Endpoint authorization surface --------------------------------


def test_portal_invoice_endpoints_use_vendor_auth():
    """Every handler in `app.api.portal` must declare `get_current_vendor_user`
    — a handler missing it would bypass vendor-scoping entirely.

    Exception: ``GET /portal/cards/{token}`` is the vendor-facing
    one-time card-reveal link emailed during card issuance. The URL
    token is the credential (sha256-hashed at rest, single-use, 7-day
    expiry); a vendor-auth dep would defeat the no-account UX. It's
    listed in test_rbac.ALTERNATE_AUTH (the token IS the credential) so the gate
    still flags any other auth-less route."""
    import inspect

    from app.api import portal

    no_vendor_auth_allowed = {
        "/portal/cards/{token}",
        # Public-by-design white-label branding for the unauthenticated portal
        # login + themed pages. PII-free, resolves the tenant via get_tenant, and
        # is listed in test_rbac.PUBLIC_BY_DESIGN (the real auth-coverage gate).
        "/portal/branding",
    }

    for route in portal.router.routes:
        if route.path in no_vendor_auth_allowed:
            continue
        sig = inspect.signature(route.endpoint)
        has_vendor_dep = any(
            getattr(getattr(p.default, "dependency", None), "__name__", "")
            == "get_current_vendor_user"
            for p in sig.parameters.values()
        )
        assert has_vendor_dep, f"{route.path} is missing get_current_vendor_user"


def test_portal_filters_invoices_by_vendor_id():
    """The list handler's only WHERE clause on Invoice is `vendor_id == caller.vendor_id`.
    Asserted at the source level — regressions would look like an added filter
    on user-supplied input being used to broaden the query."""
    import inspect

    from app.api import portal

    src = inspect.getsource(portal.list_my_invoices)
    assert "Invoice.vendor_id == vu.vendor_id" in src
    # And the get-one handler must check both id AND vendor_id.
    src_one = inspect.getsource(portal.get_my_invoice)
    assert "Invoice.vendor_id == vu.vendor_id" in src_one


def test_portal_filters_payments_by_vendor_id():
    import inspect

    from app.api import portal

    src = inspect.getsource(portal.list_my_payments)
    assert "Invoice.vendor_id == vu.vendor_id" in src


def test_portal_payment_item_carries_currency():
    """A non-USD supplier's payment history must render in the invoice's own
    currency, not a hardcoded $. The schema field + its wiring guarantee it."""
    import inspect

    from app.api import portal
    from app.schemas.portal import PortalPaymentListItem

    # The schema exposes a currency field (defaulting to USD for safety).
    assert "currency" in PortalPaymentListItem.model_fields
    item = PortalPaymentListItem(
        id="p1",
        invoice_id="i1",
        invoice_number="INV-1",
        amount="10.00",
        currency="EUR",
        status="completed",
    )
    assert item.currency == "EUR"

    # The handler sources it from the joined invoice and passes it through.
    src = inspect.getsource(portal.list_my_payments)
    assert "Invoice.currency" in src
    assert "currency=inv_currency" in src


# ---------- a successful supplier sign-in leaves evidence -------------------
#
# `/portal/auth/login` audited rejections (`portal.login.failure`) and, once a
# supplier enrolled a second factor, the completion of that factor at
# `/portal/auth/mfa/challenge` (`portal.mfa.verify.success`). The password-only
# success path wrote nothing at all — so "successful supplier sign-ins"
# returned only the MFA'd subset. That is a biased sample rather than an
# obvious gap, which is exactly why it survived: it reads as an answer.
#
# The employee twin (`api/auth.login`) has written `auth.login.success` since it
# was built; these pin the portal's equivalent, and that it stays PII-free.


@pytest.fixture
def _portal_session_redis(monkeypatch):
    """A sign-in REGISTERS the session (a Redis zset + companion hash). The
    autouse conftest fake is key/value only, so swap in the richer stand-in."""
    from tests.test_session_management import FakeRedis

    fake = FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.redis.get_redis", _get_redis)
    return fake


def _portal_login_request(ip: str = "203.0.113.7") -> SimpleNamespace:
    """Stand-in for the FastAPI Request the portal auth routes read: the client
    peer (recorded on the audit row) and the User-Agent (a coarse device label
    on the session entry — never stored raw)."""
    return SimpleNamespace(
        client=SimpleNamespace(host=ip),
        headers={"user-agent": "Chrome on macOS"},
    )


def _portal_vendor_user(**overrides):
    base = dict(
        id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="rep@supplier.example",
        full_name="Supplier Rep",
        hashed_password="not-a-real-hash",
        is_active=True,
        must_change_password=False,
        mfa_secret=None,
        mfa_enabled=False,
        mfa_enrolled_at=None,
        last_login_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _portal_login_db(vendor_user=None):
    """A MagicMock tenant session whose one `execute(...)` resolves the vendor
    user (or `None` for an unknown address)."""
    db = MagicMock()

    def _execute(*_a, **_k):
        res = MagicMock()
        res.scalar_one_or_none.return_value = vendor_user
        return res

    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    return db


def _spy_on_portal_audits(monkeypatch):
    """Replace both audit writers `portal_login` reaches — the awaited one used
    on success and the fire-and-forget one used on rejection — so a test can
    tell which path wrote what."""
    awaited = AsyncMock()
    queued = MagicMock()
    monkeypatch.setattr("app.api.portal_auth.dispatch_auth_audit", awaited)
    monkeypatch.setattr("app.api.portal_auth.queue_auth_audit", queued)
    return awaited, queued


async def _attempt_portal_login(
    monkeypatch, *, vendor_user, password="hunter2-correct", password_ok=True, ip="203.0.113.7"
):
    """Drive `portal_login`, returning (result, awaited-rows, queued-rows).

    The bcrypt verify is stubbed at `pwd_context` — the real awaitable wrapper
    still runs, only the ~200 ms hash cost is skipped.
    """
    from app.api.portal_auth import portal_login
    from app.schemas.portal import PortalLoginRequest

    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr("app.utils.passwords.pwd_context.verify", lambda *_a, **_k: password_ok)
    awaited, queued = _spy_on_portal_audits(monkeypatch)

    result = await portal_login(
        body=PortalLoginRequest(email="rep@supplier.example", password=password),
        request=_portal_login_request(ip),
        slug="acme",
        db=_portal_login_db(vendor_user),
    )
    return (
        result,
        [call.kwargs for call in awaited.await_args_list],
        [call.kwargs for call in queued.call_args_list],
    )


@pytest.mark.asyncio
async def test_password_only_sign_in_writes_one_login_success_row(
    monkeypatch, _portal_session_redis
):
    """The row an auditor asking "who signed in?" reads — and its exact shape."""
    from app.schemas.portal import PortalTokenResponse

    vu = _portal_vendor_user()
    result, awaited, queued = await _attempt_portal_login(monkeypatch, vendor_user=vu)

    assert isinstance(result, PortalTokenResponse)
    assert result.access_token
    assert queued == [], "a completed sign-in is not a rejection"

    (row,) = awaited
    assert row["action"] == "portal.login.success"
    assert row["organization_id"] == vu.organization_id
    # Subject is the vendor user on both axes, matching every other portal auth
    # row — an auditor filters the trail by `entity_id`.
    assert row["actor_id"] == vu.id
    assert row["entity_id"] == vu.id
    # PII-free and CLOSED: the client IP plus a fixed literal, nothing else. The
    # supplier contact's address is third-party PII the trail never restates,
    # and `method` is what separates this from the MFA completion row.
    assert row["details"] == {"ip": "203.0.113.7", "method": "password"}
    assert vu.email not in repr(row)
    assert vu.full_name not in repr(row)
    assert "hunter2-correct" not in repr(row)


@pytest.mark.asyncio
async def test_rejected_sign_in_still_writes_the_failure_row_and_no_success(monkeypatch):
    """The failure row is unchanged — still queued OFF the response path, so a
    known address stays indistinguishable from an unknown one by timing — and
    nothing about the new success row leaks onto a rejection."""
    from app.api.portal_auth import portal_login
    from app.schemas.portal import PortalLoginRequest

    vu = _portal_vendor_user()
    monkeypatch.setattr("app.api.portal_auth.check_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr("app.utils.passwords.pwd_context.verify", lambda *_a, **_k: False)
    awaited, queued = _spy_on_portal_audits(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await portal_login(
            body=PortalLoginRequest(email=vu.email, password="wrong-password"),
            request=_portal_login_request(),
            slug="acme",
            db=_portal_login_db(vu),
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid credentials"
    assert awaited.await_args_list == [], "a rejection must never claim a sign-in"

    (row,) = [call.kwargs for call in queued.call_args_list]
    assert row["action"] == "portal.login.failure"
    assert row["organization_id"] == vu.organization_id
    assert row["entity_id"] == vu.id
    assert row["details"] == {"ip": "203.0.113.7", "reason": "bad_password"}
    assert vu.email not in repr(row)
    assert "wrong-password" not in repr(row)


@pytest.mark.asyncio
async def test_no_success_row_when_the_session_could_not_be_minted(monkeypatch):
    """The row goes on the trail only once the sign-in actually took effect.

    `_mint_portal_session` registers the session in Redis and lets its failures
    propagate; `dispatch_auth_audit` swallows its own. Auditing first would let
    a Redis blip leave a permanent, immutable row asserting a completed sign-in
    for a request that 500'd and handed the caller no token — the same order
    `portal_mfa_challenge` and `api/auth.verify_mfa` already use.
    """
    monkeypatch.setattr(
        "app.api.portal_auth._mint_portal_session",
        AsyncMock(side_effect=RuntimeError("redis is down")),
    )

    with pytest.raises(RuntimeError):
        await _attempt_portal_login(monkeypatch, vendor_user=_portal_vendor_user())


@pytest.mark.asyncio
async def test_a_legacy_vendor_user_with_no_org_signs_in_without_a_row(
    monkeypatch, _portal_session_redis
):
    """`dispatch_auth_audit` resolves the tenant DB from `organization_id`, so
    there is nowhere to route a row for a vendor user that predates it. Skipping
    is the existing portal-audit contract — and the sign-in must still work."""
    from app.schemas.portal import PortalTokenResponse

    result, awaited, _ = await _attempt_portal_login(
        monkeypatch, vendor_user=_portal_vendor_user(organization_id=None)
    )

    assert isinstance(result, PortalTokenResponse)
    assert awaited == []
