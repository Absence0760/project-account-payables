"""1099 e-filing adapter dispatcher — picks the provider from config."""

from __future__ import annotations

from app.services.tax_filing_adapters.base import TaxFilingAdapter

_REGISTRY: dict[str, type[TaxFilingAdapter]] = {}


def register_tax_filing_adapter(provider: str):
    """Decorator that registers a 1099 e-filing adapter under a name."""

    def wrapper(cls: type[TaxFilingAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_tax_filing_adapter(config: dict | None) -> TaxFilingAdapter:
    """Create a 1099 e-filing adapter from org config.

    Config shape (``Organization.settings.tax.filing``)::

        {"provider": "mock" | "tax1099", "api_key": "...", ...}

    Falls back to ``mock`` (offline, deterministic confirmations) when
    config is empty or names an unknown provider — the local-first default.
    Deployed envs that file for real set ``provider: "tax1099"`` with a live
    key.
    """
    # Trigger registration of the built-in adapters.
    import app.services.tax_filing_adapters.mock_adapter  # noqa: F401
    import app.services.tax_filing_adapters.tax1099_adapter  # noqa: F401

    cfg = config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider) or _REGISTRY["mock"]
    return cls(cfg)
