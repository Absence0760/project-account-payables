"""Published OpenAPI spec + docs for the public ``/api/v1`` surface.

Covers:
  * the spec builder is SCOPED — only ``/api/v1`` paths, never the internal
    SPA API; no internal-only component schemas leak in
  * the spec describes the ``X-API-Key`` security scheme (applied globally),
    the ``V1Invoice`` shape (money as a JSON string), pagination params, a
    ``servers`` entry, and ``info.version == "v1"``
  * ``GET /api/v1/openapi.json`` serves it and ``GET /api/v1/docs`` renders
    Swagger UI — both gated by ``FEOH_PUBLIC_API_ENABLED`` (404 when off)

These are pure / app-level — no DB harness needed (the spec is generated from
the route table, and the endpoints don't authenticate).
"""

from __future__ import annotations

import httpx
import pytest

from app.api.v1_openapi import PUBLIC_API_VERSION, build_public_openapi
from app.config import settings
from app.main import app


def _spec() -> dict:
    return build_public_openapi(app)


# ---------------------------------------------------------------------------
# Pure spec-builder tests (no client).
# ---------------------------------------------------------------------------


def test_spec_is_scoped_to_v1_paths_only():
    spec = _spec()
    paths = spec["paths"]
    assert paths, "spec must contain the v1 paths"
    # Every documented path is under the public prefix.
    assert all(p.startswith("/api/v1") for p in paths), paths
    # The actual public read routes are present.
    assert "/api/v1/invoices" in paths
    assert "/api/v1/invoices/{invoice_id}" in paths
    # The meta endpoints (the spec/docs themselves) are NOT part of the contract.
    assert "/api/v1/openapi.json" not in paths
    assert "/api/v1/docs" not in paths
    # No internal SPA routes leaked in.
    assert not any(
        p.startswith(("/api/invoices", "/api/auth", "/api/admin", "/api/payments")) for p in paths
    )


def test_spec_info_version_and_servers():
    spec = _spec()
    assert spec["info"]["version"] == PUBLIC_API_VERSION == "v1"
    servers = spec.get("servers")
    assert servers and servers[0]["url"] == settings.api_public_url.rstrip("/")


def test_spec_has_api_key_security_scheme_applied_globally():
    spec = _spec()
    schemes = spec["components"]["securitySchemes"]
    assert "ApiKeyAuth" in schemes
    scheme = schemes["ApiKeyAuth"]
    assert scheme["type"] == "apiKey"
    assert scheme["in"] == "header"
    assert scheme["name"] == "X-API-Key"
    # Applied globally so every operation shows it.
    assert {"ApiKeyAuth": []} in spec["security"]


def test_spec_publishes_v1_invoice_money_as_string():
    spec = _spec()
    schemas = spec["components"]["schemas"]
    assert "V1Invoice" in schemas
    assert "V1InvoiceList" in schemas
    amount = schemas["V1Invoice"]["properties"]["amount"]
    # Money is serialised as a JSON string on the public contract (exactness),
    # not a number — the PlainSerializer drives the OpenAPI type to string.
    assert amount.get("type") == "string", amount


def test_spec_does_not_leak_internal_only_schemas():
    spec = _spec()
    schema_names = set(spec["components"]["schemas"])
    # The full internal app has many schemas; the public spec must carry only
    # those reachable from the v1 routes. A sampling of internal-only models
    # must NOT appear.
    for internal in ("UserResponse", "LoginRequest", "PaymentResponse", "VendorResponse"):
        assert internal not in schema_names, f"internal schema leaked: {internal}"


def test_spec_documents_pagination_params():
    spec = _spec()
    params = spec["paths"]["/api/v1/invoices"]["get"].get("parameters", [])
    names = {p["name"] for p in params}
    assert {"status", "page", "page_size"} <= names, names


# ---------------------------------------------------------------------------
# Endpoint behaviour (ASGI client) — gated by the kill switch.
# ---------------------------------------------------------------------------


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_openapi_json_served_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "public_api_enabled", True)
    async with _client() as client:
        r = await client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    body = r.json()
    assert body["info"]["version"] == "v1"
    assert "/api/v1/invoices" in body["paths"]


@pytest.mark.asyncio
async def test_docs_page_renders_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "public_api_enabled", True)
    async with _client() as client:
        r = await client.get("/api/v1/docs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # Swagger UI references the scoped spec, not the internal /openapi.json.
    assert "/api/v1/openapi.json" in r.text


@pytest.mark.asyncio
async def test_spec_and_docs_404_when_kill_switch_off(monkeypatch):
    monkeypatch.setattr(settings, "public_api_enabled", False)
    async with _client() as client:
        spec = await client.get("/api/v1/openapi.json")
        docs = await client.get("/api/v1/docs")
    assert spec.status_code == 404
    assert docs.status_code == 404
