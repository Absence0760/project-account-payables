"""ERP adapter dispatcher — picks the right adapter based on org config."""

from __future__ import annotations

from app.services.erp_adapters.base import ErpAdapter

# Registry of available adapters by erp_type
_ADAPTER_REGISTRY: dict[str, type[ErpAdapter]] = {}

# Matches `payment_adapters.dispatcher` — bound an absurd settings value out
# of log lines and HTTP bodies.
_ADAPTER_KEY_ECHO_LIMIT = 50


class UnknownErpAdapterError(ValueError):
    """`settings.erp` selects an ERP we have no adapter for.

    Raised instead of substituting `mock`, whose `post_invoice` reports every
    push as accepted — see `get_erp_adapter`.
    """

    def __init__(self, adapter_key: str):
        self.adapter_key = str(adapter_key)[:_ADAPTER_KEY_ECHO_LIMIT]
        super().__init__(
            f"No ERP adapter registered for '{self.adapter_key}'. "
            f"Registered adapters: {', '.join(list_available_adapters())}."
        )


def register_adapter(erp_type: str):
    """Decorator to register an adapter class."""

    def wrapper(cls: type[ErpAdapter]):
        _ADAPTER_REGISTRY[erp_type] = cls
        return cls

    return wrapper


def get_erp_adapter(erp_config: dict) -> ErpAdapter:
    """Create the appropriate adapter based on org ERP config.

    Config shape:
        {
            "type": "merge_dev" | "dynamics_365_bc" | "netsuite" | ...,
            "integration_method": "merge_dev" | "direct",
            ...adapter-specific fields...
        }

    If integration_method is "merge_dev", the MergeDevAdapter is used
    regardless of the ERP type. Otherwise, a direct adapter is used. Note that
    `integration_method` DEFAULTS to "merge_dev" (unchanged here), so a config
    naming only a `type` routes through Merge.dev — every caller already
    refuses an org with no `settings.erp` at all before reaching this, and
    `services/erp` passes an explicit `{"type": "mock", "integration_method":
    "direct"}` as its local-first default.

    **A selected adapter we don't have → `UnknownErpAdapterError`.** This used
    to fall back to `mock`, which is not an inert stub: `post_invoice` returns
    `success=True` with a fabricated `MOCK-…` document id, so `services/erp`
    walked the invoice `sending_to_erp → sent_to_erp → done` and recorded an
    ERP reference pointing at nothing — the invoice reads as posted to an ERP
    that never saw it. `POST /api/organization/test-erp` answered "Connected
    successfully" for the same reason (`mock.test_connection` returns True),
    so the endpoint that exists to catch the misconfiguration confirmed it
    instead. `app/main.py` already boot-guards `FEOH_AUDIT_SHIPPING_PROVIDERS`
    against its registry for exactly this failure; here the name comes from
    per-org DB settings, so the refusal lives at the dispatcher. Same call as
    `payment_adapters.dispatcher`; see `decisions.md` §29.
    """
    integration_method = erp_config.get("integration_method", "merge_dev")
    erp_type = erp_config.get("type") or "mock"

    if integration_method == "merge_dev":
        adapter_key = "merge_dev"
    else:
        adapter_key = erp_type

    adapter_cls = _ADAPTER_REGISTRY.get(adapter_key)
    if adapter_cls is None:
        raise UnknownErpAdapterError(adapter_key)

    return adapter_cls(erp_config)


def list_available_adapters() -> list[str]:
    """Return list of registered adapter type names."""
    return sorted(_ADAPTER_REGISTRY.keys())
