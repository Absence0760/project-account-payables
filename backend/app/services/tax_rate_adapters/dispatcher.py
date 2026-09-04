"""Tax-rate adapter dispatcher — picks the provider from org config."""

from __future__ import annotations

from app.services.tax_rate_adapters.base import TaxRateAdapter

_REGISTRY: dict[str, type[TaxRateAdapter]] = {}

# How much of an admin-supplied provider name may be echoed back in an error.
# Bounded so an absurd settings value can't bloat a log line or an HTTP body.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownTaxRateProviderError(ValueError):
    """``settings.tax.rate_provider`` names a rate provider we have no adapter for.

    Raised instead of silently substituting ``mock``, which is not an inert
    stub: it answers every country from the in-repo country-rules table. That
    table is a plausible *fixture*, not a maintained rate feed — the reason an
    org configures Avalara or TaxJar at all is that statutory rates change and a
    hardcoded one goes stale silently. One typo in an admin-entered provider
    name therefore computed VAT / GST for `POST /api/international-tax/vat` and
    `/gst` off the fixture while the response's ``provider`` field named the
    provider that was asked for, so nothing anywhere said the figure was not the
    jurisdiction's current rate.

    ``decisions.md`` §29 applied to this dispatcher family. An *absent or empty*
    provider still resolves ``mock`` — the local-first default, and the country
    rules are genuinely the right answer with no cloud account.
    """

    def __init__(self, provider: str):
        # Admin-supplied config, not PII — bounded anyway.
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No tax-rate adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(list_available_providers())}."
        )


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

    **No provider → ``mock``** (rates from the in-repo country-rules engine):
    the local-first default, so a fresh clone computes VAT / GST with no cloud
    account.

    **A named provider we have no adapter for →
    :class:`UnknownTaxRateProviderError`** — see that docstring. The three
    ``/api/international-tax`` routes that reach a rate turn it into a 409
    naming the bad value; they are pure compute and persist nothing, so there is
    no half-written state to unwind.
    """
    # Trigger registration of the built-in adapters. The refusal below is only
    # trustworthy if every built-in adapter has had a chance to register.
    import app.services.tax_rate_adapters.avalara  # noqa: F401
    import app.services.tax_rate_adapters.mock_adapter  # noqa: F401
    import app.services.tax_rate_adapters.taxjar  # noqa: F401
    from app.config import settings

    cfg = dict(tax_config or {})
    # Per-org `rate_provider` wins; otherwise fall back to the platform-wide
    # default (FEOH_TAX_RATE_PROVIDER). Mock when neither names a provider.
    provider = (cfg.get("rate_provider") or settings.tax_rate_provider or "mock").lower()
    # Surface the platform API key to a cloud adapter when the org didn't set
    # its own (kept out of logs; the adapter only reads it).
    cfg.setdefault("api_key", settings.tax_rate_api_key)
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise UnknownTaxRateProviderError(provider)
    return cls(cfg)


def list_available_providers() -> list[str]:
    return sorted(_REGISTRY.keys())
