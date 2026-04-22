"""Security-header middleware tests.

Covers the SOC 2 transport-layer hardening added in
`docs/soc2-readiness.md` § Encryption:

- HSTS toggles on `AP_HSTS_ENABLED`.
- `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` are always
  set — they aren't HTTPS-dependent, so gating them behind the HSTS flag
  would just give auditors something to flag in local dev.

We hit `/api/health` rather than standing up a fixture router — it's
public, has no DB dependency, and already exists for the live server's
health check.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.main import app


@pytest.fixture
def _restore_hsts_flag():
    """Snapshot + restore AP_HSTS_ENABLED so tests don't leak state."""
    original = settings.hsts_enabled
    try:
        yield
    finally:
        settings.hsts_enabled = original


async def _get_headers() -> httpx.Headers:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")
        assert response.status_code == 200
        return response.headers


async def test_hsts_header_present_when_enabled(_restore_hsts_flag):
    settings.hsts_enabled = True
    headers = await _get_headers()
    hsts = headers.get("strict-transport-security")
    assert hsts is not None
    assert f"max-age={settings.hsts_max_age}" in hsts
    # Default config keeps both directives on — verify we render them.
    assert "includeSubDomains" in hsts
    assert "preload" in hsts


async def test_hsts_header_absent_when_disabled(_restore_hsts_flag):
    settings.hsts_enabled = False
    headers = await _get_headers()
    assert "strict-transport-security" not in headers


async def test_tablestakes_security_headers_always_set(_restore_hsts_flag):
    # The three non-HSTS headers have no HTTP/HTTPS dependency and must
    # appear regardless of the HSTS flag.
    for flag_value in (False, True):
        settings.hsts_enabled = flag_value
        headers = await _get_headers()
        assert headers.get("x-content-type-options") == "nosniff"
        assert headers.get("x-frame-options") == "DENY"
        assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"
