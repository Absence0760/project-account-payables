"""Unit tests for the cross-tenant guard in `app.tenant.get_tenant`.

The guard refuses to resolve a tenant if the caller's JWT identifies
a different organization than the slug. Without it, an authenticated
user from tenant A could read or mutate tenant B's data by swapping
the `X-Tenant-Slug` header — a violation of project invariant #4
(tenant isolation at the data layer).

The check lives in `get_tenant` (not the deeper `get_tenant_db`) so
every endpoint that pulls the Organization object is covered, not
only the ones that also open a per-tenant DB session.

Tests exercise the dependency directly with a fake control-plane
session and a real-signed JWT so the decode path runs end-to-end.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from jose import jwt

from app.config import settings


def _mint(payload: dict) -> str:
    """Sign a JWT with the app's real secret so `decode_token` accepts it."""
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _ctrl_db_returning(org):
    """Mock the control-plane session whose first execute() yields
    the Organization row (or None for the 404 path)."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=org)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _fake_org(org_id: uuid.UUID, slug: str = "acme") -> SimpleNamespace:
    return SimpleNamespace(id=org_id, slug=slug, db_name=f"ap_{slug}")


# ---------------------------------------------------------------------------
# Mismatch / match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tenant_refuses_mismatched_org_claim():
    """The headline bug: techflow JWT + X-Tenant-Slug:acme used to
    resolve the acme Organization. Now it must raise 403."""
    from app.tenant import get_tenant

    acme_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    token = _mint({"sub": str(uuid.uuid4()), "org": str(other_org_id), "typ": "user", "jti": "j1"})

    with pytest.raises(HTTPException) as exc:
        await get_tenant(
            slug="acme",
            db=_ctrl_db_returning(_fake_org(acme_id, "acme")),
            authorization=f"Bearer {token}",
        )

    assert exc.value.status_code == 403
    assert "tenant" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_get_tenant_allows_matching_org_claim():
    """Positive control — same flow with a matching org claim
    returns the Organization."""
    from app.tenant import get_tenant

    acme_id = uuid.uuid4()
    token = _mint({"sub": str(uuid.uuid4()), "org": str(acme_id), "typ": "user", "jti": "j2"})

    org = await get_tenant(
        slug="acme",
        db=_ctrl_db_returning(_fake_org(acme_id, "acme")),
        authorization=f"Bearer {token}",
    )
    assert org.id == acme_id


@pytest.mark.asyncio
async def test_get_tenant_404_for_unknown_slug_takes_precedence_over_mismatch():
    """Slugs first, then claims. An unknown slug returns 404 before
    we check the claim — otherwise the response code would leak
    "this slug exists" vs "doesn't exist" to an attacker who has
    *any* valid JWT."""
    from app.tenant import get_tenant

    token = _mint({"sub": str(uuid.uuid4()), "org": str(uuid.uuid4()), "typ": "user", "jti": "j3"})

    with pytest.raises(HTTPException) as exc:
        await get_tenant(
            slug="not-real",
            db=_ctrl_db_returning(None),
            authorization=f"Bearer {token}",
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Vendor / no-auth / malformed-auth pass-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tenant_skips_check_for_vendor_portal_tokens():
    """Vendor-portal tokens (typ=vendor) are exempt — VendorUser
    rows live in the tenant DB, so cross-tenant attempts fail
    naturally on the user-lookup query downstream."""
    from app.tenant import get_tenant

    acme_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    token = _mint(
        {"sub": str(uuid.uuid4()), "org": str(other_org_id), "typ": "vendor", "jti": "j4"}
    )

    org = await get_tenant(
        slug="acme",
        db=_ctrl_db_returning(_fake_org(acme_id, "acme")),
        authorization=f"Bearer {token}",
    )
    assert org.id == acme_id


@pytest.mark.asyncio
async def test_get_tenant_skips_check_when_no_authorization_header():
    """An unauthenticated request must reach the downstream auth dep
    (which will then 401). The tenant guard's job is to prevent
    cross-tenant leakage, not to short-circuit auth."""
    from app.tenant import get_tenant

    acme_id = uuid.uuid4()
    org = await get_tenant(
        slug="acme",
        db=_ctrl_db_returning(_fake_org(acme_id, "acme")),
        authorization=None,
    )
    assert org.id == acme_id


@pytest.mark.asyncio
async def test_get_tenant_skips_check_when_authorization_is_unparseable():
    """A malformed bearer (garbage signature, wrong alg) must not
    cause a 500 inside the tenant guard — let the downstream auth
    dependency raise the canonical 401."""
    from app.tenant import get_tenant

    acme_id = uuid.uuid4()
    org = await get_tenant(
        slug="acme",
        db=_ctrl_db_returning(_fake_org(acme_id, "acme")),
        authorization="Bearer not-a-jwt",
    )
    assert org.id == acme_id


@pytest.mark.asyncio
async def test_get_tenant_skips_check_when_token_has_no_org_claim():
    """A token missing the `org` claim (legacy or hand-rolled) falls
    through to the downstream user lookup — which will fail closed
    when it tries to derive an organization_id from the User row.
    The guard must not 500 on a missing claim."""
    from app.tenant import get_tenant

    acme_id = uuid.uuid4()
    token = _mint({"sub": str(uuid.uuid4()), "typ": "user", "jti": "j5"})  # no org claim

    org = await get_tenant(
        slug="acme",
        db=_ctrl_db_returning(_fake_org(acme_id, "acme")),
        authorization=f"Bearer {token}",
    )
    assert org.id == acme_id


# ---------------------------------------------------------------------------
# Authorization-header parsing edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tenant_ignores_non_bearer_authorization():
    """`Authorization: Basic xyz` (or any non-Bearer prefix) must
    not be parsed as a JWT. The guard's "no auth header" branch
    must catch this — pass through, let downstream auth dep reject."""
    from app.tenant import get_tenant

    acme_id = uuid.uuid4()
    org = await get_tenant(
        slug="acme",
        db=_ctrl_db_returning(_fake_org(acme_id, "acme")),
        authorization="Basic dXNlcjpwYXNz",
    )
    assert org.id == acme_id
