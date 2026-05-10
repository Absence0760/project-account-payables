"""FX adapter dispatcher — picks the right provider from org config."""

from __future__ import annotations

from app.services.fx_adapters.base import FXAdapter

_REGISTRY: dict[str, type[FXAdapter]] = {}


def register_fx_adapter(provider: str):
    """Decorator that registers an FX adapter under a provider name."""

    def wrapper(cls: type[FXAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_fx_adapter(fx_config: dict | None) -> FXAdapter:
    """Create an FX adapter from org config.

    Config shape:
        {
            "provider": "mock" | "openexchangerates" | ...,
            "api_key": "...",
            ...provider-specific fields...
        }

    Falls back to the `mock` adapter when config is empty or names an
    unknown provider — sensible default for local dev and tests, and
    fails closed in prod because the mock returns a fixed rate that
    will not match real market.
    """
    # Trigger registration of the built-in adapters.
    import app.services.fx_adapters.mock_adapter  # noqa: F401
    import app.services.fx_adapters.openexchangerates  # noqa: F401

    cfg = fx_config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider) or _REGISTRY["mock"]
    return cls(cfg)
