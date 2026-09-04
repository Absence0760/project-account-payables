"""QMS adapter dispatcher — picks the provider from org config."""

from __future__ import annotations

from app.services.qms_adapters.base import QMSAdapter

_REGISTRY: dict[str, type[QMSAdapter]] = {}

# How much of an admin-supplied provider name may be echoed back in an error.
# Bounded so an absurd settings value can't bloat a log line or an HTTP body.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownQmsProviderError(ValueError):
    """``settings.qms.provider`` names a QMS we have no adapter for.

    Raised instead of silently substituting ``mock``, which is not an inert
    stub: it returns three deterministic fixtures (``QMS-INSP-001 pass /
    PO-1001`` …) that ``qms_sync`` resolves against the tenant's REAL purchase
    orders and persists as ``completed`` ``QualityInspection`` rows,
    indistinguishable from real ones in the UI. Those rows are the 4-way
    match's quality leg, so a fabricated ``pass`` clears the quality gate for
    whatever invoice references that PO — a purchase order cleared for payment
    by an inspection that never happened — and a fabricated ``fail`` flips real
    invoices to ``mismatch``.

    ``resolve_opted_in_qms_config`` already refuses to hand ``mock`` fixtures to
    an org that never opted in. This closes the other half: an org that DID opt
    in, with a value we cannot honour. It is the sharper half for the platform
    override — a typo'd ``FEOH_QMS_PROVIDER`` opts **every** org in at once, so
    the old fallback pulled fixtures into every tenant in the estate on the next
    tick.

    ``decisions.md`` §29 applied to this dispatcher family. An *absent or empty*
    provider still resolves ``mock`` — the local-first default, and the only
    caller that can reach it that way is a test or a dev fixture, since both
    production entry points gate on ``resolve_opted_in_qms_config`` first.
    """

    def __init__(self, provider: str):
        # Admin-supplied config, not PII — bounded anyway.
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No QMS adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(list_available_providers())}."
        )


def register_qms_adapter(provider: str):
    """Decorator that registers a QMS adapter under a provider name."""

    def wrapper(cls: type[QMSAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_qms_adapter(qms_config: dict | None) -> QMSAdapter:
    """Resolve the configured QMS provider.

    Config shape (read from ``Organization.settings.qms``):
        {
            "provider": "mock" | "generic" | ...,
            "base_url": "...",
            "api_key": "...",
            ...provider-specific fields...
        }

    **No provider → ``mock``** (deterministic fixtures, no network, no
    credential): the local-first default, so a fresh clone exercises the whole
    4-way match with no QMS account.

    **A named provider we have no adapter for →
    :class:`UnknownQmsProviderError`** — see that docstring for what the old
    fallback bought. Each caller decides what the refusal means: the background
    sweep counts a per-tenant failure and does NOT advance that org's
    ``last_synced_at`` cursor (advancing it would skip the window forever once
    the config is corrected); ``POST /api/inspections/sync`` 409s naming the bad
    value, because an operator asked for that pull directly and deserves to be
    told why it did not happen.

    Real providers (``generic``) still fail closed without a ``base_url`` +
    ``api_key`` rather than silently inventing inspection rows.
    """
    # Trigger registration of the built-in adapters. The refusal below is only
    # trustworthy if every built-in adapter has had a chance to register.
    import app.services.qms_adapters.generic_qms  # noqa: F401
    import app.services.qms_adapters.mock_adapter  # noqa: F401

    cfg = qms_config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise UnknownQmsProviderError(provider)
    return cls(cfg)


def list_available_providers() -> list[str]:
    return sorted(_REGISTRY.keys())
