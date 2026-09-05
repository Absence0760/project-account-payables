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
    (§36 did the same for sanctions). An *unset* provider is a normal state and
    never raises — it resolves through `get_default_provider`, which hands back
    the region's preferred issuer only when a credential for it exists and
    `LOCAL_FIRST_PROVIDER` otherwise. Callers on the money path turn this raise
    into a refusal; best-effort callers (upstream cancel, vendor PAN reveal)
    degrade and record the reason.
    """

    def __init__(self, provider: str):
        # `provider` is admin-supplied config, not PII — but bound it anyway so
        # an oversized value can't bloat a log line or a response body.
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No card adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(list_available_providers())}."
        )


# The local-first default (guard rail 7). Every other adapter registry in this
# codebase resolves an *unconfigured* provider to its fixture adapter so a dev
# laptop with no cloud account can run the whole app; cards resolved an unset
# provider straight to a REAL ISSUER instead, so a fresh clone's
# `POST /api/cards/generate` reached out to Lithic. `REGION_DEFAULTS` below is
# therefore a *preference*, applied only once this deployment (or the org's own
# BYOK config) actually holds a credential for the preferred issuer.
LOCAL_FIRST_PROVIDER = "mock"

# What has to be present for a region preference to be real, on each of the two
# credential sources. `mock` — and any future credential-free adapter — is
# absent from the map and therefore always usable.
_PLATFORM_CREDENTIAL_FIELDS: dict[str, tuple[str, ...]] = {
    # `app.config.Settings` attribute names (platform program: our keys).
    "lithic": ("lithic_api_key",),
    "nium": ("nium_client_id", "nium_client_secret"),
}
_BYOK_CREDENTIAL_FIELDS: dict[str, tuple[str, ...]] = {
    # `settings.cards.*` keys as `_resolve_card_config` shapes them for a BYOK
    # program (the customer's own keys).
    "lithic": ("api_key",),
    "nium": ("client_id", "client_secret"),
}

# Default provider per region — a PREFERENCE, not the resolution. Read it
# through `region_preference()` / `get_default_provider()`, never directly.
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

    **No provider → the region preference, but only if it is reachable.** An
    org that has never named an issuer is a normal state, so this never raises;
    it resolves to `REGION_DEFAULTS[region]` when a credential for that issuer
    is present (on this config, for a BYOK program, or on the deployment, for a
    platform one) and to `LOCAL_FIRST_PROVIDER` when there is none. That is what
    keeps a fresh clone — guard rail 7 — from calling a real issuer.

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
        preferred = region_preference(region)
        provider = (
            preferred if _has_usable_credentials(preferred, card_config) else LOCAL_FIRST_PROVIDER
        )

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        raise UnknownCardProviderError(provider)

    return adapter_cls(card_config)


def region_preference(region: str) -> str:
    """Which issuer this region PREFERS, credentials aside.

    The pure `REGION_DEFAULTS` lookup, split out from `get_default_provider` so
    the region map stays independently readable and drift-guardable. Callers
    choosing an adapter want `get_default_provider`, which also asks whether
    that preference is reachable at all.
    """
    return REGION_DEFAULTS.get(region, "nium")


def _has_usable_credentials(provider: str, card_config: dict | None = None) -> bool:
    """Can `provider` actually be reached from here?

    Two credential sources, either of which counts: the org's own BYOK keys on
    the card config it handed us, and this deployment's platform keys. A
    provider that needs neither (``mock``) is always usable.
    """
    platform_fields = _PLATFORM_CREDENTIAL_FIELDS.get(provider)
    if platform_fields is None:
        return True

    if card_config:
        byok_fields = _BYOK_CREDENTIAL_FIELDS.get(provider, ())
        if any(str(card_config.get(field) or "").strip() for field in byok_fields):
            return True

    # Imported lazily: the dispatcher is a leaf module the adapters import, and
    # reading the singleton at call time is what lets an operator's env (or a
    # test) decide.
    from app.config import settings

    return any(str(getattr(settings, field, "") or "").strip() for field in platform_fields)


def get_default_provider(region: str) -> str:
    """The provider to use when the org has named none.

    The region preference **if this deployment holds a platform credential for
    it**, else the local-first `mock`. Without the credential check a fresh
    clone — which has no `FEOH_LITHIC_API_KEY` / `FEOH_NIUM_CLIENT_ID` — issued
    cards by calling Lithic's real host with an empty key, which guard rail 7
    says must not happen. An operator who has configured a real issuer keeps
    region-based selection exactly as before.

    This is deliberately NOT the same thing as substituting `mock` for a
    provider someone NAMED: that stays a hard `UnknownCardProviderError`
    (`decisions.md` §29 / §56). This path only picks a default where nobody
    expressed a preference at all.
    """
    preferred = region_preference(region)
    return preferred if _has_usable_credentials(preferred) else LOCAL_FIRST_PROVIDER


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
