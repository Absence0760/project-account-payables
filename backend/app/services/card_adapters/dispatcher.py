"""Card adapter dispatcher — picks the right provider based on org config and region."""

from __future__ import annotations

from app.services.card_adapters.base import CardAdapter

_ADAPTER_REGISTRY: dict[str, type[CardAdapter]] = {}

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

    If provider is not specified, auto-select based on region.
    """
    provider = card_config.get("provider")
    region = card_config.get("region", "US")

    if not provider:
        provider = REGION_DEFAULTS.get(region, "nium")

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if not adapter_cls:
        adapter_cls = _ADAPTER_REGISTRY.get("mock")
        if not adapter_cls:
            raise ValueError(f"No card adapter registered for '{provider}' and no mock fallback")

    return adapter_cls(card_config)


def get_default_provider(region: str) -> str:
    """Return the default card provider for a given region."""
    return REGION_DEFAULTS.get(region, "nium")


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
