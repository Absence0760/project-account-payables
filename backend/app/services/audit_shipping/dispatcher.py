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

    Raises on an unregistered name — a typo'd `FEOH_AUDIT_SHIPPING_PROVIDERS`
    entry (e.g. "cloudwath") must fail loud, not silently substitute the
    no-op `mock` adapter. `mock` "succeeding" on every ship() would let
    `audit_log_shipper` stamp every row `shipped_at` while nothing ever
    reaches the real sink — defeating the SOC 2 WORM/tamper-evidence control
    with no signal (issue #164). `mock` is still available, but only when
    named explicitly.
    """
    provider = config.get("provider", "mock")

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        raise ValueError(
            f"No audit-shipping adapter registered for '{provider}' — "
            f"registered providers: {', '.join(list_available_providers())}"
        )

    return adapter_cls(config)


def get_audit_shipping_adapters(providers: list[str], config: dict) -> list[AuditShippingAdapter]:
    """Build one adapter per name in `providers`, sharing the same config.

    Callers typically pass a list like ["cloudwatch", "s3_objectlock"]
    so the shipper can fan the same batch out to multiple sinks.
    """
    return [get_audit_shipping_adapter({**config, "provider": p}) for p in providers]


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
