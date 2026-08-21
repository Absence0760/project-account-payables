"""Card adapter dispatcher — picks the right provider based on org config and region."""

from __future__ import annotations

from app.services.card_adapters.base import CardAdapter

_ADAPTER_REGISTRY: dict[str, type[CardAdapter]] = {}

# How much of an admin-supplied provider name may be echoed back in an error.
# The column it comes from is `String(50)`; bounding it keeps an absurd
# settings value out of a log line or an HTTP body.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownCardProviderError(ValueError):
    """`settings.cards.provider` names an issuer we have no adapter for.

    Raised instead of silently substituting `mock`, whose `create_card` returns
    `success=True` with a synthetic `mock_card_…` id and `last_four="4242"` and
    whose `get_card_details` hands back the fixture PAN `4242424242424242`. A
    single typo in an admin-entered provider name therefore made every issuance
    "succeed": cards landed with `card_provider="mock"`, the payment-run card
    leg marked each payment `completed` and each invoice `payment_scheduled`,
    and vendors were emailed reveal links resolving to a fixture PAN — no money
    moved and nothing failed.

    This is `decisions.md` §29 applied to the one dispatcher family it missed
    (§36 did the same for sanctions). An *unset* provider still resolves via
    `REGION_DEFAULTS` — that is the local-first default and a normal state.
    Callers on the money path turn this into a refusal; best-effort callers
    (upstream cancel, vendor PAN reveal) degrade and record the reason.
    """

    def __init__(self, provider: str):
        # `provider` is admin-supplied config, not PII — but bound it anyway so
        # an oversized value can't bloat a log line or a response body.
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No card adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(list_available_providers())}."
        )


# Default provider per region
REGION_DEFAULTS: dict[str, str] = {
    "US": "lithic",
    "UK": "lithic",
    "GB": "lithic",
    # EU member states (all 27) — Lithic has an EU entity; routing a EUR/SEPA
    # vendor to Nium by omission could fail at the terminal or add FX fees.
    "AT": "lithic",  # Austria
    "BE": "lithic",  # Belgium
    "BG": "lithic",  # Bulgaria
    "HR": "lithic",  # Croatia
    "CY": "lithic",  # Cyprus
    "CZ": "lithic",  # Czechia
    "DK": "lithic",  # Denmark
    "EE": "lithic",  # Estonia
    "FI": "lithic",  # Finland
    "FR": "lithic",  # France
    "DE": "lithic",  # Germany
    "GR": "lithic",  # Greece
    "EL": "lithic",  # Greece (EU/ISO alt code)
    "HU": "lithic",  # Hungary
    "IE": "lithic",  # Ireland
    "IT": "lithic",  # Italy
    "LV": "lithic",  # Latvia
    "LT": "lithic",  # Lithuania
    "LU": "lithic",  # Luxembourg
    "MT": "lithic",  # Malta
    "NL": "lithic",  # Netherlands
    "PL": "lithic",  # Poland
    "PT": "lithic",  # Portugal
    "RO": "lithic",  # Romania
    "SK": "lithic",  # Slovakia
    "SI": "lithic",  # Slovenia
    "ES": "lithic",  # Spain
    "SE": "lithic",  # Sweden
    # EEA / EFTA non-EU but SEPA — also Lithic
    "NO": "lithic",  # Norway
    "IS": "lithic",  # Iceland
    "LI": "lithic",  # Liechtenstein
    "CH": "lithic",  # Switzerland
    # Rest of world → Nium
    "ZA": "nium",
    "AU": "nium",
    "NZ": "nium",
    "SG": "nium",
    "HK": "nium",
    "IN": "nium",
    "CA": "nium",
    "BR": "nium",
    "MX": "nium",
    "AE": "nium",
    "JP": "nium",
}


def register_card_adapter(provider: str):
    """Decorator to register a card adapter class."""

    def wrapper(cls: type[CardAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_card_adapter(card_config: dict) -> CardAdapter:
    """Create the appropriate card adapter based on org config.

    Config shape:
        {
            "provider": "lithic" | "nium" | "mock",
            "region": "US" | "ZA" | "UK" | ...,
            ...provider-specific fields...
        }

    **No provider → the region default** (unchanged): that is the local-first
    default, and an org that has never named an issuer is a normal state.

    **A configured provider we have no adapter for →
    ``UnknownCardProviderError``.** This used to fall back to ``mock``, which is
    not an inert stub — see the exception's docstring for what a single typo in
    ``settings.cards.provider`` bought. Same call `decisions.md` §29 made for
    the payment / ERP / FX dispatchers and §36 for sanctions.
    """
    # Make the registry authoritative regardless of what the caller imported:
    # the refusal below is only trustworthy if every built-in adapter has had a
    # chance to register itself. Mirrors `get_enrichment_adapter`.
    import app.services.card_adapters.lithic  # noqa: F401
    import app.services.card_adapters.mock_adapter  # noqa: F401
    import app.services.card_adapters.nium  # noqa: F401

    provider = card_config.get("provider")
    region = card_config.get("region", "US")

    if not provider:
        provider = REGION_DEFAULTS.get(region, "nium")

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        raise UnknownCardProviderError(provider)

    return adapter_cls(card_config)


def get_default_provider(region: str) -> str:
    """Return the default card provider for a given region."""
    return REGION_DEFAULTS.get(region, "nium")


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
