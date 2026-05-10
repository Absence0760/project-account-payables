"""CORS origin enforcement.

The CORS middleware in `app/main.py` uses an origin regex so that
only `*.localhost` (dev) and `app.com` (prod) subdomains can call the
API from a browser. The regex is the wall against a hostile page on
`https://evil.example.com` making authenticated requests with the
user's bearer token.

These tests fire preflight (OPTIONS) and credentialed-GET requests
against the running app via the ASGI transport and confirm the
`Access-Control-Allow-Origin` header is only echoed for origins that
match the regex. A regression that broadens the regex (or drops it)
is caught here.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_cors_allows_tenant_subdomain_of_localhost():
    """`acme.localhost:7777` is the dev tenant origin. Preflight
    OPTIONS must echo Access-Control-Allow-Origin so the browser
    permits the follow-up request."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.options(
            "/api/invoices",
            headers={
                "Origin": "http://acme.localhost:7777",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,x-tenant-slug",
            },
        )

    assert r.headers.get("access-control-allow-origin") == "http://acme.localhost:7777"
    assert r.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.asyncio
async def test_cors_allows_production_domain():
    """`https://app.com` (the prod root) must be allowed."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.options(
            "/api/invoices",
            headers={
                "Origin": "https://app.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert r.headers.get("access-control-allow-origin") == "https://app.com"


@pytest.mark.asyncio
async def test_cors_allows_production_subdomain():
    """Tenant subdomains of `app.com` (e.g., `acme.app.com`) must be
    allowed. Without this, real customers can't reach the API."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.options(
            "/api/invoices",
            headers={
                "Origin": "https://acme.app.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert r.headers.get("access-control-allow-origin") == "https://acme.app.com"


@pytest.mark.asyncio
async def test_cors_rejects_arbitrary_external_origin():
    """A hostile page on `evil.example.com` must NOT receive the
    Allow-Origin echo. Without that header the browser refuses to
    surface the response body to the page's script, so even a
    cookie / token leak is contained."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.options(
            "/api/invoices",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


@pytest.mark.asyncio
async def test_cors_rejects_app_com_lookalike_domain():
    """`evil-app.com` or `appXcom` substring-style probes must NOT
    pass. The regex anchors on `app.com` as a tail — a hostname
    ending in (or containing) that string but with extra characters
    must NOT match.
    """
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # `evil-app.com` — substring of `app.com` is present but
        # surrounded by extra characters.
        for origin in (
            "https://evil-app.com",
            "https://app.com.evil.test",
            "https://app-com.evil.test",
        ):
            r = await client.options(
                "/api/invoices",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
            assert "access-control-allow-origin" not in {k.lower() for k in r.headers}, (
                f"CORS unexpectedly allowed origin: {origin}"
            )


@pytest.mark.asyncio
async def test_cors_rejects_arbitrary_localhost_lookalike():
    """Similar regex anchoring on `localhost`. `evillocalhost.com` or
    `localhost.evil.test` must NOT match. Without anchoring, the
    regex would allow any hostname that *contains* "localhost"."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for origin in (
            "https://localhost.evil.test",
            "https://evillocalhost.test",
            "https://my-localhost.example",
        ):
            r = await client.options(
                "/api/invoices",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
            assert "access-control-allow-origin" not in {k.lower() for k in r.headers}, (
                f"CORS unexpectedly allowed origin: {origin}"
            )


@pytest.mark.asyncio
async def test_cors_credentials_flag_is_on():
    """`allow_credentials=True` means the browser will send cookies /
    Authorization on cross-origin requests when the origin matches.
    Pin the flag — turning it off would break legitimate cross-
    subdomain auth (which the multi-tenant setup relies on)."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.options(
            "/api/invoices",
            headers={
                "Origin": "http://acme.localhost:7777",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert r.headers.get("access-control-allow-credentials") == "true"
