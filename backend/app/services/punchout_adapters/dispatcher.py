"""Punch-out adapter dispatcher — selects the adapter from config.

Mirrors ``peppol_adapters/dispatcher.py``: a registry decorator
(``@register_punchout_adapter``) + a ``get_punchout_adapter`` that picks the
provider from the per-org ``Organization.settings.punchout`` block, falling back
to the process-level ``FEOH_PUNCHOUT_PROVIDER`` (default ``mock``) when none is
named — the local-first default, so a fresh clone runs a whole punch-out
round-trip with no supplier credential — and **refusing a NAMED provider it has
no adapter for**.

Falling back to ``mock`` for a named-but-unknown provider is the shape
``decisions.md`` §29 removed from the payment / ERP / FX dispatchers and §36
from sanctions. The mock is not an inert stub: ``build_setup_request`` returns a
synthetic in-process start URL with no supplier contacted, which
``start_punchout_session`` persists as a ``PunchoutSession`` row stamped
``provider="mock"`` and then navigates the buyer to; and ``parse_order_message``
reads a permissive dev envelope, so the PUBLIC cart-return endpoint accepted a
different payload shape than the tenant's configured protocol — and the fixture
cart it produced converts into a real ``PurchaseRequisition``.
"""

from __future__ import annotations

from app.config import settings
from app.services.punchout_adapters.base import PunchoutAdapter

_ADAPTER_REGISTRY: dict[str, type[PunchoutAdapter]] = {}

# How much of an admin-supplied provider name may be echoed back in an error.
# Bounded so an absurd settings value can't bloat a log line or an HTTP body.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownPunchoutProviderError(ValueError):
    """``settings.punchout.provider`` names a provider we have no adapter for.

    Raised instead of silently substituting ``mock`` — see the module docstring
    for what that bought. Callers turn it into a refusal: the start route 422s
    with the PII-free code ``punchout_provider_not_configured`` and persists no
    session; the public cart-return endpoint drops the cart silently, like every
    other rejection there.
    """

    def __init__(self, provider: str):
        # Admin-supplied config, not PII — bounded anyway.
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No punch-out adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(list_available_providers())}."
        )


def register_punchout_adapter(provider: str):
    """Decorator to register a punch-out adapter class under a provider name."""

    def wrapper(cls: type[PunchoutAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_punchout_adapter(punchout_config: dict | None) -> PunchoutAdapter:
    """Build the adapter for the configured provider.

    Config shape (lives in ``Organization.settings.punchout``)::

        {
            "provider": "mock" | "cxml",
            "shared_secret": "...",      # cxml only — sops in deployed; no fallback
            "buyer_identity": "...",     # our network/org id in the cXML header
            "protocol": "cxml" | "oci"   # cxml adapter supports both shapes
        }

    **No provider → ``mock``** (in-process, no supplier): the local-first
    default. **A named provider we have no adapter for →
    :class:`UnknownPunchoutProviderError`.**
    """
    config = punchout_config or {}
    provider = config.get("provider") or settings.punchout_provider  # default "mock"
    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        raise UnknownPunchoutProviderError(provider)
    return adapter_cls(config)


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
