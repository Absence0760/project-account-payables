"""Audit-log shipping adapter dispatcher + registry.

Mirrors the ERP / extraction / email adapter pattern: a decorator
registers a class under a name, and a dispatcher builds one by name.
"""

from __future__ import annotations

from app.services.audit_shipping.base import AuditShippingAdapter

_ADAPTER_REGISTRY: dict[str, type[AuditShippingAdapter]] = {}


def register_audit_shipping_adapter(provider: str):
    """Decorator to register an audit-shipping adapter under `provider`."""

    def wrapper(cls: type[AuditShippingAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_audit_shipping_adapter(config: dict) -> AuditShippingAdapter:
    """Build a single adapter by `config["provider"]`.

    Falls back to the `mock` adapter if the named provider isn't
    registered — intentional so a bad env var on a running server
    degrades to an in-memory sink (which logs a loud warning) rather
    than crashing the shipper loop on boot.
    """
    provider = config.get("provider", "mock")

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        adapter_cls = _ADAPTER_REGISTRY.get("mock")
        if adapter_cls is None:
            raise ValueError(f"No audit-shipping adapter registered for '{provider}'")

    return adapter_cls(config)


def get_audit_shipping_adapters(providers: list[str], config: dict) -> list[AuditShippingAdapter]:
    """Build one adapter per name in `providers`, sharing the same config.

    Callers typically pass a list like ["cloudwatch", "s3_objectlock"]
    so the shipper can fan the same batch out to multiple sinks.
    """
    return [get_audit_shipping_adapter({**config, "provider": p}) for p in providers]


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
