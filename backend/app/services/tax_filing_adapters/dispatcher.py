"""1099 e-filing adapter dispatcher — picks the provider from config."""

from __future__ import annotations

from app.services.tax_filing_adapters.base import TaxFilingAdapter

_REGISTRY: dict[str, type[TaxFilingAdapter]] = {}

# How much of an admin-supplied provider name may be echoed back in an error.
# The column it is persisted to (`tax_1099_filings.provider`) is `String(50)`;
# bounding it keeps an absurd settings value out of a log line or an HTTP body.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownTaxFilingProviderError(ValueError):
    """``settings.tax.filing.provider`` names a filing partner we have no
    adapter for.

    Raised instead of silently substituting ``mock``, which is not an inert
    stub: it "accepts" every well-formed batch and returns a deterministic
    ``MOCK-<year>-<hex>`` confirmation number. ``POST /api/tax/1099/file``
    persists that verdict on a ``Tax1099Filing`` row, writes a ``tax_1099.filed``
    audit row and answers 200 — so one typo in an admin-entered provider name
    told the org its 1099s were e-filed when nothing ever reached the IRS, and
    the idempotency slot was burned so the honest retry became a no-op.

    ``decisions.md`` §29 applied to this dispatcher family (§36 did the same
    for sanctions). An *absent or empty* provider still resolves ``mock`` —
    that is the local-first default and a normal state.
    """

    def __init__(self, provider: str):
        # Admin-supplied config, not PII — bounded anyway so an oversized value
        # can't bloat a log line or a response body.
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No 1099 e-filing adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(list_available_providers())}."
        )


def register_tax_filing_adapter(provider: str):
    """Decorator that registers a 1099 e-filing adapter under a name."""

    def wrapper(cls: type[TaxFilingAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_tax_filing_adapter(config: dict | None) -> TaxFilingAdapter:
    """Create a 1099 e-filing adapter from org config.

    Config shape (``Organization.settings.tax.filing``)::

        {"provider": "mock" | "tax1099", "api_key": "...", ...}

    **No provider → ``mock``** (offline, deterministic confirmations): the
    local-first default, so a fresh clone runs the whole filing flow with no
    partner account.

    **A named provider we have no adapter for →
    :class:`UnknownTaxFilingProviderError`** — see that docstring for what the
    old fallback bought. The API layer turns it into a clean 409 *before* the
    idempotency slot is claimed, so no filing row, no fake confirmation and no
    audit row asserting one.

    Deployed envs that file for real set ``provider: "tax1099"`` with a live
    key; that adapter still fails closed without one rather than pretending.
    """
    # Trigger registration of the built-in adapters. The refusal below is only
    # trustworthy if every built-in adapter has had a chance to register.
    import app.services.tax_filing_adapters.mock_adapter  # noqa: F401
    import app.services.tax_filing_adapters.tax1099_adapter  # noqa: F401

    cfg = config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise UnknownTaxFilingProviderError(provider)
    return cls(cfg)


def list_available_providers() -> list[str]:
    return sorted(_REGISTRY.keys())
