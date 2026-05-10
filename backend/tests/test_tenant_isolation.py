"""Unit tests for the cross-tenant guard in `app.tenant.get_tenant_db`.

The guard refuses to yield a session if the caller's JWT identifies a
different organization than the tenant resolved from `X-Tenant-Slug`.
Without it, an authenticated user from tenant A could read tenant B's
rows just by swapping the header — a violation of project invariant
#4 (tenant isolation at the data layer).

We exercise the dependency directly with a fake tenant Organization
and a real-signed JWT so the decode path runs end-to-end. Engine setup
is mocked so the test stays DB-free.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from app.config import settings


def _mint(payload: dict) -> str:
    """Sign a JWT with the app's real secret so `decode_token` accepts it."""
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _fake_tenant(org_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(id=org_id, db_name="ap_acme")


async def _drain(gen):
    """Run an async generator and surface the first yielded value (or the
    exception). `get_tenant_db` is an async generator dependency."""
    try:
        return await gen.__anext__()
    finally:
        # Suppress engine cleanup — the engine is mocked.
        try:
            await gen.__anext__()
        except (StopAsyncIteration, Exception):
            pass


@pytest.mark.asyncio
async def test_tenant_db_refuses_mismatched_org_claim():
    """The headline bug: techflow JWT + X-Tenant-Slug:acme used to
    return a working acme session. Now it must raise 403."""
    from app.tenant import get_tenant_db

    acme_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    tenant = _fake_tenant(acme_id)
    token = _mint(
        {"sub": str(uuid.uuid4()), "org": str(other_org_id), "typ": "user", "jti": "j1"}
    )

    with pytest.raises(HTTPException) as exc:
        gen = get_tenant_db(tenant=tenant, authorization=f"Bearer {token}")
        await _drain(gen)

    assert exc.value.status_code == 403
    assert "tenant" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_tenant_db_allows_matching_org_claim():
    """Positive control — the same flow with a matching org claim
    yields a session normally."""
    from app.tenant import get_tenant_db

    acme_id = uuid.uuid4()
    tenant = _fake_tenant(acme_id)
    token = _mint(
        {"sub": str(uuid.uuid4()), "org": str(acme_id), "typ": "user", "jti": "j2"}
    )

    with (
        patch("app.tenant.get_tenant_engine", return_value=MagicMock()),
        patch("app.tenant.async_sessionmaker") as mk_factory,
    ):
        session = AsyncMock()
        mk_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=session)
        mk_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=False)

        gen = get_tenant_db(tenant=tenant, authorization=f"Bearer {token}")
        yielded = await _drain(gen)

    assert yielded is session


@pytest.mark.asyncio
async def test_tenant_db_skips_check_for_vendor_portal_tokens():
    """Vendor-portal tokens (typ=vendor) are exempt — VendorUser rows
    live in the tenant DB, so cross-tenant attempts fail naturally on
    the user-lookup query downstream. The tenant guard must not block
    the path."""
    from app.tenant import get_tenant_db

    acme_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    tenant = _fake_tenant(acme_id)
    # Vendor token with a different org claim — guard should ignore it.
    token = _mint(
        {"sub": str(uuid.uuid4()), "org": str(other_org_id), "typ": "vendor", "jti": "j3"}
    )

    with (
        patch("app.tenant.get_tenant_engine", return_value=MagicMock()),
        patch("app.tenant.async_sessionmaker") as mk_factory,
    ):
        session = AsyncMock()
        mk_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=session)
        mk_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=False)

        gen = get_tenant_db(tenant=tenant, authorization=f"Bearer {token}")
        yielded = await _drain(gen)

    assert yielded is session


@pytest.mark.asyncio
async def test_tenant_db_skips_check_when_no_authorization_header():
    """An unauthenticated request must reach the downstream auth dep
    (which will then 401). The tenant guard's job is to prevent
    cross-tenant leakage, not to short-circuit auth."""
    from app.tenant import get_tenant_db

    tenant = _fake_tenant(uuid.uuid4())

    with (
        patch("app.tenant.get_tenant_engine", return_value=MagicMock()),
        patch("app.tenant.async_sessionmaker") as mk_factory,
    ):
        session = AsyncMock()
        mk_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=session)
        mk_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=False)

        gen = get_tenant_db(tenant=tenant, authorization=None)
        yielded = await _drain(gen)

    assert yielded is session


@pytest.mark.asyncio
async def test_tenant_db_skips_check_when_authorization_is_unparseable():
    """A malformed bearer (no token, garbage signature) must not
    cause a 500 inside the tenant guard — let the downstream auth
    dependency raise the canonical 401."""
    from app.tenant import get_tenant_db

    tenant = _fake_tenant(uuid.uuid4())

    with (
        patch("app.tenant.get_tenant_engine", return_value=MagicMock()),
        patch("app.tenant.async_sessionmaker") as mk_factory,
    ):
        session = AsyncMock()
        mk_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=session)
        mk_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=False)

        gen = get_tenant_db(tenant=tenant, authorization="Bearer not-a-jwt")
        yielded = await _drain(gen)

    assert yielded is session
