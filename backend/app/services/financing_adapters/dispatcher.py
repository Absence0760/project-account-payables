"""Supplier-financing adapter dispatcher — picks the provider from org config."""

from __future__ import annotations

from app.services.financing_adapters.base import FinancingAdapter

_REGISTRY: dict[str, type[FinancingAdapter]] = {}

# Matches `payment_adapters.dispatcher` / `fx_adapters.dispatcher` — bound an
# absurd settings value out of log lines and HTTP bodies.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownFinancingProviderError(ValueError):
    """`settings.financing.provider` names a financier we have no adapter for.

    Raised instead of substituting `mock`, whose `request_funding` reports a
    supplier as funded — see `get_financing_adapter`.
    """

    def __init__(self, provider: str):
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No financing adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(sorted(_REGISTRY))}."
        )


def register_financing_adapter(provider: str):
    """Decorator that registers a financing adapter under a provider name."""

    def wrapper(cls: type[FinancingAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_financing_adapter(financing_config: dict | None) -> FinancingAdapter:
    """Resolve the configured supplier-financing provider.

    Config shape (read from `Organization.settings.financing`):
        {
            "provider": "mock" | "c2fo" | ...,
            "api_key": "...",
            ...provider-specific fields...
        }

    **No configured provider → `mock`**: the local-first default, so a fresh
    clone quotes supplier financing with no cloud account. Deterministic
    quotes, no network, no credential.

    **A configured provider we have no adapter for → `UnknownFinancingProviderError`.**
    This used to fall back to `mock` too, leaving this the last dispatcher in
    the codebase still failing open — `payment_adapters`, `erp_adapters` and
    `fx_adapters` all close on a NAMED unknown provider (`decisions.md` §29).
    The mock is not an inert stub: `MockFinancingAdapter.request_funding`
    returns `funded=True` with a fabricated `mock-fund-<hash>` id, which is the
    call that reports a supplier as PAID by a financier. A one-character typo
    in `Organization.settings.financing.provider` would therefore record a
    funded advance no financier ever saw. Closed before the first production
    caller lands rather than after.

    Real providers (`c2fo`) still fail closed without an `api_key` rather than
    silently quoting a fake offer.
    """
    # Trigger registration of the built-in adapters.
    import app.services.financing_adapters.c2fo  # noqa: F401
    import app.services.financing_adapters.mock_adapter  # noqa: F401

    cfg = financing_config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise UnknownFinancingProviderError(provider)
    return cls(cfg)
