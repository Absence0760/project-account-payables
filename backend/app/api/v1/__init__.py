"""Public versioned API surface — ``/api/v1``.

Authenticated with a programmatic API key (the ``X-API-Key`` header), NOT the
SPA's JWT Bearer session. The key resolves to its organization and tenant DB at
the data layer via ``deps.get_api_key_db`` (the same ``get_tenant_engine``
chokepoint the JWT path uses), so every read here is tenant-isolated.

This is the FIRST read slice: list + fetch invoices over the resolved tenant in
a STABLE serialized shape (``schemas/public_v1``) that is decoupled from the
internal ORM models. Outbound webhooks and a published OpenAPI spec are later
slices — not built here.
"""

from app.api.v1.invoices import router

__all__ = ["router"]
