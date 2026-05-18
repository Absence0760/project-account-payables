"""CORS origin enforcement.

The CORS middleware in `app/main.py` builds its origin regex at boot
from two settings: ``cors_origins`` (exact-match list, default
includes localhost dev origins) and ``cors_production_domain`` (the
real deploy domain, empty by default). The regex is the wall against
a hostile page on `https://evil.example.com` making authenticated
requests with the user's bearer token.

These tests fire preflight (OPTIONS) and credentialed-GET requests
against the running app via the ASGI transport — they reflect the
*default* config (no production domain set). The production-domain
case is covered by the `_build_cors_origin_regex` unit tests below
because the middleware is bound at import time and can't be reset
without rebuilding the FastAPI app.
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


def test_build_cors_origin_regex_with_no_production_domain():
    """Default config: only `localhost` (any subdomain + port) matches.
    Nothing else — including the prior placeholder `app.com` — is in
    the allowlist."""
    import re

    from app.main import _build_cors_origin_regex

    with patched_setting("cors_production_domain", ""):
        pattern = re.compile(_build_cors_origin_regex())
    assert pattern.fullmatch("http://localhost:7777")
    assert pattern.fullmatch("https://acme.localhost")
    assert not pattern.fullmatch("https://app.com")
    assert not pattern.fullmatch("https://acme.app.com")


def test_build_cors_origin_regex_with_configured_production_domain():
    """When ``AP_CORS_PRODUCTION_DOMAIN`` is set, the regex picks up
    both the root domain and any subdomain. Multi-domain deploys can
    pass a comma-separated list."""
    import re

    from app.main import _build_cors_origin_regex

    with patched_setting("cors_production_domain", "example.com"):
        pattern = re.compile(_build_cors_origin_regex())
    assert pattern.fullmatch("https://example.com")
    assert pattern.fullmatch("https://acme.example.com")
    assert pattern.fullmatch("http://acme.example.com")
    assert not pattern.fullmatch("https://evil.com")
    assert not pattern.fullmatch("https://evilexample.com")

    with patched_setting("cors_production_domain", "ap.example.com, ap-staging.example.com"):
        pattern = re.compile(_build_cors_origin_regex())
    assert pattern.fullmatch("https://acme.ap.example.com")
    assert pattern.fullmatch("https://acme.ap-staging.example.com")
    assert not pattern.fullmatch("https://other.example.com")


def test_build_cors_origin_regex_dots_are_escaped():
    """A naive ``f-string`` injection would let ``a.com`` match
    ``axcom`` because ``.`` is the regex wildcard. The builder must
    escape the domain so only the literal dot counts."""
    import re

    from app.main import _build_cors_origin_regex

    with patched_setting("cors_production_domain", "ap.example.com"):
        pattern = re.compile(_build_cors_origin_regex())
    assert not pattern.fullmatch("https://apxexamplexcom")


from contextlib import contextmanager  # noqa: E402


@contextmanager
def patched_setting(name: str, value: str):
    """Temporarily override a settings attribute around a block."""
    from app.config import settings

    original = getattr(settings, name)
    setattr(settings, name, value)
    try:
        yield
    finally:
        setattr(settings, name, original)


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
async def test_cors_rejects_unconfigured_production_origin():
    """``app.com`` was the prior hardcoded placeholder. With the
    production domain now empty by default, the running app must NOT
    allow it (or any other arbitrary hostname)."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for origin in (
            "https://app.com",
            "https://acme.app.com",
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
