"""Tax-rate adapter dispatcher — picks the provider from org config."""

from __future__ import annotations

from app.services.tax_rate_adapters.base import TaxRateAdapter

_REGISTRY: dict[str, type[TaxRateAdapter]] = {}


def register_tax_rate_adapter(provider: str):
    """Decorator that registers a tax-rate adapter under a provider name."""

    def wrapper(cls: type[TaxRateAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_tax_rate_adapter(tax_config: dict | None) -> TaxRateAdapter:
    """Create a tax-rate adapter from org config.

    Config shape (``Organization.settings.tax``)::

        {
            "rate_provider": "mock" | "avalara" | "taxjar",
            "api_key": "...",
            ...provider-specific fields...
        }

    Falls back to the ``mock`` adapter when config is empty or names an
    unknown provider — the local-first default. The mock resolves rates
    from the country-rules engine, so a fresh clone needs no cloud account.
    """
    # Trigger registration of the built-in adapters.
    import app.services.tax_rate_adapters.avalara  # noqa: F401
    import app.services.tax_rate_adapters.mock_adapter  # noqa: F401
    import app.services.tax_rate_adapters.taxjar  # noqa: F401
    from app.config import settings

    cfg = dict(tax_config or {})
    # Per-org `rate_provider` wins; otherwise fall back to the platform-wide
    # default (AP_TAX_RATE_PROVIDER). Mock when neither names a real provider.
    provider = (cfg.get("rate_provider") or settings.tax_rate_provider or "mock").lower()
    # Surface the platform API key to a cloud adapter when the org didn't set
    # its own (kept out of logs; the adapter only reads it).
    cfg.setdefault("api_key", settings.tax_rate_api_key)
    cls = _REGISTRY.get(provider) or _REGISTRY["mock"]
    return cls(cfg)
