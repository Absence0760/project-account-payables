"""FX adapter dispatcher — picks the right provider from org config."""

from __future__ import annotations

from app.services.fx_adapters.base import FXAdapter

_REGISTRY: dict[str, type[FXAdapter]] = {}

# Matches `payment_adapters.dispatcher` — bound an absurd settings value out
# of log lines and HTTP bodies.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownFxProviderError(ValueError):
    """`settings.fx.provider` names a rate source we have no adapter for.

    Raised instead of substituting `mock`, whose rate is a hardcoded table —
    see `get_fx_adapter`.
    """

    def __init__(self, provider: str):
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No FX adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(sorted(_REGISTRY))}."
        )


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

    **No configured provider → `mock`** (unchanged): the local-first default,
    so a fresh clone converts currencies with no cloud account.

    **A configured provider we have no adapter for → `UnknownFxProviderError`.**
    This used to fall back to `mock` as well, and the docstring claimed that
    "fails closed in prod because the mock returns a fixed rate that will not
    match real market". It does the opposite. `MockFxAdapter.get_rate` returns
    a plausible rate off a hardcoded table, and
    `international_payments.prepare_international_payment` LOCKS whatever it
    gets onto the Payment row (`fx_rate`, `fx_locked_at`, `source_amount`) —
    the figure that drives the actual outflow and, later,
    `realized_fx_gain_loss_for_settlement`. A one-character typo in
    `settings.fx.provider` therefore produced a confidently wrong, persisted,
    never-re-fetched rate rather than any kind of refusal. Same call as
    `payment_adapters.dispatcher`; see `decisions.md` §29.
    """
    # Trigger registration of the built-in adapters.
    import app.services.fx_adapters.mock_adapter  # noqa: F401
    import app.services.fx_adapters.openexchangerates  # noqa: F401

    cfg = fx_config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise UnknownFxProviderError(provider)
    return cls(cfg)
