"""PEPPOL adapter dispatcher — selects the Access Point adapter from config."""

from __future__ import annotations

from app.config import settings
from app.services.peppol_adapters.base import PeppolAdapter

_ADAPTER_REGISTRY: dict[str, type[PeppolAdapter]] = {}

# How much of an admin-supplied provider name may be echoed back in an error.
# Bounded so an absurd settings value can't bloat a log line or an HTTP body.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownPeppolProviderError(ValueError):
    """``settings.peppol.provider`` names an Access Point we have no adapter for.

    Raised instead of silently substituting ``mock``, which is not an inert
    stub: its ``send`` returns ``success=True`` with a synthetic message id and
    no network involved at all. ``peppol_send`` writes that outcome onto a
    ``PeppolTransmission`` row as ``status="sent"`` with a ``message_id``, emits
    an ``invoice.peppol_sent`` audit row, and answers 200 — so one typo in an
    admin-entered provider name reported a legally-significant e-invoice as
    transmitted to a supplier that never received it, and the row occupied the
    live-transmission slot so the honest resend was refused as ``already_sent``.

    ``decisions.md`` §29 applied to this dispatcher family. An *absent or empty*
    provider still resolves ``mock`` — the local-first default, so a fresh clone
    exercises the whole four-corner flow with no PEPPOL credential.
    """

    def __init__(self, provider: str):
        # Admin-supplied config, not PII — bounded anyway.
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No PEPPOL adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(list_available_providers())}."
        )


def register_peppol_adapter(provider: str):
    """Decorator to register a PEPPOL adapter class under a provider name."""

    def wrapper(cls: type[PeppolAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_peppol_adapter(peppol_config: dict | None) -> PeppolAdapter:
    """Build the adapter for the configured provider.

    Config shape (lives in ``Organization.settings.peppol``)::

        {
            "provider": "mock" | "as4_gateway",
            "gateway_url": "...",         # as4_gateway only
            "api_key": "...",             # as4_gateway only — sops in deployed
            "sender_scheme": "9930",      # our (C1) EAS scheme
            "sender_value": "DE...",      # our registered id
            "webhook_secret": "..."       # inbound HMAC verify
        }

    **No provider → ``mock``** (in-process, no network): the local-first default
    and a normal state for an org that has never onboarded to an Access Point.

    **A named provider we have no adapter for →
    :class:`UnknownPeppolProviderError`** — see that docstring for what the old
    fallback bought. ``peppol_send`` resolves the adapter *before* it claims the
    transmission slot, so the refusal leaves no row claiming a transmission
    happened; the inbound webhook treats it as our own failure and asks the
    Access Point to redeliver rather than acking a document it dropped.
    """
    config = peppol_config or {}
    provider = config.get("provider") or settings.peppol_provider  # default "mock"
    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        raise UnknownPeppolProviderError(provider)
    return adapter_cls(config)


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
