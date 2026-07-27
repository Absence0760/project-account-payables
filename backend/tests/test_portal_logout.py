"""Portal logout must only revoke vendor-portal tokens.

`POST /portal/auth/logout` reads the raw `Authorization` header (no dependency)
and blocks the token's jti in the shared Redis blocklist. Without a `typ` guard
it accepted ANY JWT signed with `FEOH_SECRET_KEY` — including an employee
`typ=user` token — so the public portal-logout route could revoke an employee
session. These tests pin the symmetric `typ == "vendor"` guard.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.deps import create_access_token, create_vendor_access_token
from app.api.portal_auth import portal_logout


@pytest.mark.asyncio
async def test_logout_rejects_employee_token_without_blocking_it():
    """An employee `typ=user` JWT must be refused (401) and its jti must NEVER
    reach the blocklist — otherwise a vendor could revoke an employee session
    from the public portal-logout endpoint."""
    employee_token = create_access_token(uuid.uuid4(), uuid.uuid4())

    with patch("app.api.portal_auth.block_token", AsyncMock()) as block:
        with pytest.raises(HTTPException) as exc:
            await portal_logout(authorization=f"Bearer {employee_token}")
    assert exc.value.status_code == 401
    block.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_accepts_vendor_token_and_blocks_jti():
    """A genuine vendor-portal token is revoked — its jti is blocklisted."""
    vendor_token = create_vendor_access_token(uuid.uuid4(), uuid.uuid4())

    with patch("app.api.portal_auth.block_token", AsyncMock()) as block:
        await portal_logout(authorization=f"Bearer {vendor_token}")
    block.assert_awaited_once()
