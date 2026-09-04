"""TIN-validation adapter dispatcher — picks the provider from config."""

from __future__ import annotations

from app.services.tin_validation_adapters.base import TINValidationAdapter

_REGISTRY: dict[str, type[TINValidationAdapter]] = {}

# How much of an admin-supplied provider name may be echoed back in an error.
# Bounded so an absurd settings value can't bloat a log line or an HTTP body.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownTinValidationProviderError(ValueError):
    """``settings.tax.tin_validation.provider`` names a provider we have no
    adapter for.

    Raised instead of silently substituting ``mock``, which is not an inert
    stub: it checks digit grouping and the IRS's published invalid patterns and
    nothing else. It never reaches the IRS, so it cannot tell whether a
    well-formed TIN is actually *assigned* to the named entity — yet
    ``POST /api/tax/vendors/{id}/tin-verify`` stamps ``Vendor.tin_verified_at``
    from its verdict, and that stamp is what the 1099 dashboard reads as "TIN
    verified". One typo in an admin-entered provider name therefore turned an
    IRS TIN-match into a regex, and B-notice / 24% backup-withholding decisions
    were made off it.

    ``decisions.md`` §29 applied to this dispatcher family (§36 did the same
    for sanctions). An *absent or empty* provider still resolves ``mock`` —
    that is the local-first default and a normal state: format validation with
    no cloud account is genuinely useful, it just isn't a TIN match.
    """

    def __init__(self, provider: str):
        # Admin-supplied config, not PII — bounded anyway. Never carries a TIN.
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No TIN-validation adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(list_available_providers())}."
        )


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

    **No provider → ``mock``** (offline format + checksum only): the local-first
    default, so a fresh clone validates TIN *shape* with no cloud account. The
    mock never reaches the IRS, so it can never prove a TIN is assigned; it only
    catches malformed numbers.

    **A named provider we have no adapter for →
    :class:`UnknownTinValidationProviderError`** — see that docstring. The API
    layer turns it into a 409 and leaves ``tin_verified_at`` untouched rather
    than stamping a verification the configured provider never performed.

    Deployed envs that need real TIN-match set ``provider: "tax1099"`` with a
    live key; that adapter still fails closed without one.
    """
    # Trigger registration of the built-in adapters. The refusal below is only
    # trustworthy if every built-in adapter has had a chance to register.
    import app.services.tin_validation_adapters.mock_adapter  # noqa: F401
    import app.services.tin_validation_adapters.tax1099_adapter  # noqa: F401

    cfg = config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise UnknownTinValidationProviderError(provider)
    return cls(cfg)


def list_available_providers() -> list[str]:
    return sorted(_REGISTRY.keys())
