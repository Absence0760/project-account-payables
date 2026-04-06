"""ERP adapter dispatcher — picks the right adapter based on org config."""

from __future__ import annotations

from app.services.erp_adapters.base import ErpAdapter


# Registry of available adapters by erp_type
_ADAPTER_REGISTRY: dict[str, type[ErpAdapter]] = {}


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
    regardless of the ERP type. Otherwise, a direct adapter is used.
    """
    integration_method = erp_config.get("integration_method", "merge_dev")
    erp_type = erp_config.get("type", "mock")

    if integration_method == "merge_dev":
        adapter_key = "merge_dev"
    else:
        adapter_key = erp_type

    adapter_cls = _ADAPTER_REGISTRY.get(adapter_key)
    if not adapter_cls:
        # Fall back to mock if adapter not found
        adapter_cls = _ADAPTER_REGISTRY.get("mock")
        if not adapter_cls:
            raise ValueError(f"No adapter registered for '{adapter_key}' and no mock fallback")

    return adapter_cls(erp_config)


def list_available_adapters() -> list[str]:
    """Return list of registered adapter type names."""
    return sorted(_ADAPTER_REGISTRY.keys())
