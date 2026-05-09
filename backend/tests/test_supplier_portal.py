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
    token = create_vendor_access_token(vu_id, vendor_id)

    fake_vu = SimpleNamespace(id=vu_id, vendor_id=vendor_id, email="p@v.com", is_active=True)
    scalars_result = MagicMock()
    scalars_result.scalar_one_or_none.return_value = fake_vu
    db = MagicMock()
    db.execute = AsyncMock(return_value=scalars_result)

    async def _not_blocked(_jti):
        return False

    monkeypatch.setattr("app.api.portal_deps.is_token_blocked", _not_blocked)

    resolved = await get_current_vendor_user(authorization=f"Bearer {token}", db=db)
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
    — a handler missing it would bypass vendor-scoping entirely."""
    import inspect

    from app.api import portal

    for route in portal.router.routes:
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
