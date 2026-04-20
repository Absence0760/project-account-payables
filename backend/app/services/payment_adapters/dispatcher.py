"""Payment adapter dispatcher — selects the right processor from org config."""

from __future__ import annotations

from app.services.payment_adapters.base import PaymentAdapter

_ADAPTER_REGISTRY: dict[str, type[PaymentAdapter]] = {}


def register_payment_adapter(provider: str):
    """Decorator to register a payment adapter class."""

    def wrapper(cls: type[PaymentAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_payment_adapter(payment_config: dict | None) -> PaymentAdapter:
    """Build the adapter for the configured provider.

    Config shape (lives in `Organization.settings.payments`):

        {
            "program_type": "platform" | "byok",
            "provider": "modern_treasury" | "mock",
            "api_key": "...",            # BYOK only
            "ledger_account_id": "...",  # Modern Treasury BYOK
            "originating_account_id": "...",
            "sandbox": true              # default true
        }

    Falls back to the mock adapter when no config exists or the provider is
    unknown — keeps local dev painless and prevents a missed config from
    silently 500-ing the entire payments domain.
    """
    config = payment_config or {}
    provider = config.get("provider", "mock")
    adapter_cls = _ADAPTER_REGISTRY.get(provider) or _ADAPTER_REGISTRY.get("mock")
    if adapter_cls is None:
        raise ValueError(f"No payment adapter registered for '{provider}' and no mock fallback")
    return adapter_cls(config)


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
