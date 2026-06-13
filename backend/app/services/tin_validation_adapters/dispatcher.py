"""TIN-validation adapter dispatcher — picks the provider from config."""

from __future__ import annotations

from app.services.tin_validation_adapters.base import TINValidationAdapter

_REGISTRY: dict[str, type[TINValidationAdapter]] = {}


def register_tin_validation_adapter(provider: str):
    """Decorator that registers a TIN-validation adapter under a name."""

    def wrapper(cls: type[TINValidationAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_tin_validation_adapter(config: dict | None) -> TINValidationAdapter:
    """Create a TIN-validation adapter from org config.

    Config shape (``Organization.settings.tax.tin_validation``)::

        {"provider": "mock" | "tax1099", "api_key": "...", ...}

    Falls back to ``mock`` (offline format + checksum only) when config is
    empty or names an unknown provider — the local-first default. The mock
    never reaches the IRS, so it can never *prove* a TIN is assigned; it only
    catches malformed numbers. Deployed envs that need real TIN-match set
    ``provider: "tax1099"`` with a live key.
    """
    # Trigger registration of the built-in adapters.
    import app.services.tin_validation_adapters.mock_adapter  # noqa: F401
    import app.services.tin_validation_adapters.tax1099_adapter  # noqa: F401

    cfg = config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider) or _REGISTRY["mock"]
    return cls(cfg)
