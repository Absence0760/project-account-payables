"""Sanctions adapter dispatcher."""

from __future__ import annotations

from app.services.sanctions_adapters.base import SanctionsAdapter

_REGISTRY: dict[str, type[SanctionsAdapter]] = {}

# Matches `payment_adapters.dispatcher` / `financing_adapters.dispatcher` —
# bound an absurd settings value out of log lines and HTTP bodies.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownSanctionsProviderError(ValueError):
    """`settings.compliance.sanctions.provider` names a screening provider we
    have no adapter for.

    Raised instead of substituting `mock`, whose `screen_vendor` clears every
    name outside its own fixture list — see `get_sanctions_adapter`.
    """

    def __init__(self, provider: str):
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No sanctions adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(sorted(_REGISTRY))}."
        )


def register_sanctions_adapter(provider: str):
    """Decorator that registers a sanctions adapter under a provider name."""

    def wrapper(cls: type[SanctionsAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_sanctions_adapter(compliance_config: dict | None) -> SanctionsAdapter:
    """Resolve the configured sanctions provider.

    Config shape (read from `Organization.settings.compliance.sanctions`):
        {
            "provider": "mock" | "complyadvantage" | ...,
            "api_key": "...",
            ...provider-specific fields...
        }

    **No configured provider → `mock`**: the local-first default, so a fresh
    clone screens vendors with no cloud account. Deterministic, no network, no
    credential.

    **A configured provider we have no adapter for →
    `UnknownSanctionsProviderError`.** This used to fall back to `mock` too,
    on the strength of a docstring claiming "the compliance service surfaces a
    warning in its result so this misconfiguration is visible to the AP team"
    — a compensating control that was never built: `services/compliance` never
    inspected `adapter.provider_name` and `services/vendor_screening` recorded
    the mock's verdict unexamined. The mock is not an inert stub: it clears
    every name outside its three-entry fixture list, so one typo in
    `Organization.settings.compliance.sanctions.provider` ("worldcheck" for
    the registry's `refinitiv`, say) screened an entire tenant's vendor book
    against nothing and returned `clear` / risk 0 — on the control that exists
    to stop money reaching a sanctioned party. Same call as `erp_adapters` /
    `payment_adapters` / `fx_adapters` / `financing_adapters`.

    Both consumers fail closed on the raise rather than letting it 500:
    `compliance.check_payment_compliance` returns `hold` with a reason (the
    payment waits in `pending_compliance` for AP), and
    `vendor_screening.screen_vendor_record` records a `review_required` screen
    so the vendor lands on the screening review queue instead of reading
    `clear`.

    Real providers (`complyadvantage` / `dowjones` / `refinitiv`) still fail
    closed without an `api_key` rather than silently clearing a vendor.
    """
    # Trigger registration of the built-in adapters.
    import app.services.sanctions_adapters.complyadvantage  # noqa: F401
    import app.services.sanctions_adapters.dowjones  # noqa: F401
    import app.services.sanctions_adapters.mock_adapter  # noqa: F401
    import app.services.sanctions_adapters.refinitiv  # noqa: F401

    cfg = compliance_config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise UnknownSanctionsProviderError(provider)
    return cls(cfg)
