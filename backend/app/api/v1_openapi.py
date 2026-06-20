"""Published, versioned OpenAPI document for the public ``/api/v1`` surface.

This is the *external contract* an integrator codes against — deliberately
**scoped to the public ``/api/v1`` routes only**, NOT the whole internal SPA
API. It is generated from the live FastAPI route table (so it can't drift from
the routes) but filtered down to the ``/api/v1`` path prefix, then overlaid
with:

- a single ``X-API-Key`` security scheme (the only auth on this surface — never
  the SPA's JWT) applied globally;
- an ``info.version`` of ``v1`` and a ``servers`` entry built from
  ``AP_API_PUBLIC_URL`` so generated clients hit the right base URL;
- the published ``V1Invoice`` / ``V1InvoiceList`` component schemas only — no
  internal-only model leaks in.

Both this spec and the human-readable docs page respect the
``AP_PUBLIC_API_ENABLED`` kill switch: when the public API is off, the spec and
docs 404 (the surface is simply not there), consistent with every API-key
request failing closed.

The routes are mounted additively in ``app/main.py`` at:

- ``GET /api/v1/openapi.json`` — the machine-readable spec
- ``GET /api/v1/docs``         — Swagger UI rendered against that spec
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings

# Stable contract version of the public surface. Bumping the path prefix
# (``/api/v2``) is what introduces a new version; this string tracks it.
PUBLIC_API_VERSION = "v1"

# The path prefix (under the app's ``/api`` mount) that constitutes the public
# surface. Anything not under here is internal and must not appear in the spec.
_PUBLIC_PATH_PREFIX = "/api/v1"

# Paths that are part of the public mount but are not themselves part of the
# documented contract (the spec/docs meta-endpoints describe the contract; they
# are not a resource of it).
_META_PATHS = frozenset(
    {
        f"{_PUBLIC_PATH_PREFIX}/openapi.json",
        f"{_PUBLIC_PATH_PREFIX}/docs",
    }
)

router = APIRouter(prefix="/v1", tags=["public-v1-meta"])


def build_public_openapi(app: FastAPI) -> dict[str, Any]:
    """Build the OpenAPI document scoped to the public ``/api/v1`` routes.

    Generates the full app spec from the live route table, then keeps only the
    ``/api/v1`` paths (minus the meta endpoints), prunes orphaned component
    schemas, and overlays the ``X-API-Key`` security scheme + ``servers`` +
    contract version. Pure with respect to the app (reads routes, returns a new
    dict) — safe to call per request.
    """
    full = get_openapi(
        title="Account Payables — Public Developer API",
        version=PUBLIC_API_VERSION,
        description=(
            "Programmatic, versioned access to the Account Payables platform. "
            "Authenticated with a per-organization API key sent in the "
            "`X-API-Key` header (NOT the SPA session JWT). The key resolves to "
            "its organization and tenant data — there is no tenant header to "
            "swap. See backend/docs/public-api.md for the deprecation policy."
        ),
        routes=app.routes,
    )

    # 1. Keep only the public-surface paths, dropping the meta endpoints.
    public_paths: dict[str, Any] = {
        path: item
        for path, item in (full.get("paths") or {}).items()
        if path.startswith(_PUBLIC_PATH_PREFIX) and path not in _META_PATHS
    }
    full["paths"] = public_paths

    # 2. Prune component schemas down to those actually referenced by the kept
    #    paths (transitively) so no internal-only model leaks in via an orphan.
    all_schemas = (full.get("components") or {}).get("schemas") or {}
    kept_schema_names = _referenced_schema_names(public_paths, all_schemas)
    components: dict[str, Any] = full.get("components") or {}
    components["schemas"] = {
        name: schema for name, schema in all_schemas.items() if name in kept_schema_names
    }

    # 3. The ONLY auth on this surface: an API-key header scheme, applied
    #    globally so every documented operation shows it.
    components["securitySchemes"] = {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": (
                "Per-organization API key, format `ap_live_…`. Mint via the "
                "admin key-management API. The key is the tenant boundary."
            ),
        }
    }
    full["components"] = components
    full["security"] = [{"ApiKeyAuth": []}]

    # 4. A servers entry so generated clients hit the right base URL.
    full["servers"] = [
        {
            "url": settings.api_public_url.rstrip("/"),
            "description": "Account Payables API",
        }
    ]

    return full


def _referenced_schema_names(
    paths: dict[str, Any], all_schemas: dict[str, Any]
) -> set[str]:
    """Collect every component-schema name reachable from ``paths``.

    Walks the kept path items for ``$ref`` pointers into
    ``#/components/schemas/…`` and follows references between schemas
    transitively, so a kept schema that references another keeps that one too.
    """
    seen: set[str] = set()

    def visit_ref(ref: str) -> None:
        prefix = "#/components/schemas/"
        if not ref.startswith(prefix):
            return
        name = ref[len(prefix) :]
        if name in seen or name not in all_schemas:
            return
        seen.add(name)
        _collect_refs(all_schemas[name], visit_ref)

    for item in paths.values():
        _collect_refs(item, visit_ref)

    return seen


def _collect_refs(node: Any, on_ref) -> None:
    """Recursively invoke ``on_ref`` for every ``$ref`` string in ``node``."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                on_ref(value)
            else:
                _collect_refs(value, on_ref)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, on_ref)


def _ensure_enabled() -> None:
    """404 when the public API kill switch is off — the surface is simply gone.

    Keeps the spec/docs behaviour consistent with every API-key request, which
    fails closed when ``AP_PUBLIC_API_ENABLED`` is false. A 404 (rather than a
    distinct "disabled") doesn't confirm the endpoint exists.
    """
    if not settings.public_api_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


@router.get("/openapi.json", include_in_schema=False)
async def public_openapi(request: Request) -> JSONResponse:
    """The published OpenAPI document for the ``/api/v1`` surface (public)."""
    _ensure_enabled()
    return JSONResponse(build_public_openapi(request.app))


@router.get("/docs", include_in_schema=False)
async def public_docs() -> HTMLResponse:
    """Human-readable Swagger UI for the public ``/api/v1`` surface."""
    _ensure_enabled()
    return get_swagger_ui_html(
        openapi_url=f"{_PUBLIC_PATH_PREFIX}/openapi.json",
        title="Account Payables — Public Developer API",
    )
