"""Extraction adapter dispatcher — picks the right provider based on org config."""

from __future__ import annotations

from app.services.extraction_adapters.base import ExtractionAdapter

_ADAPTER_REGISTRY: dict[str, type[ExtractionAdapter]] = {}

# Matches `erp_adapters` / `payment_adapters` — bound an absurd settings value
# out of log lines and HTTP bodies.
_ADAPTER_KEY_ECHO_LIMIT = 50

# What an org that has configured nothing resolves to. That is a normal state
# (guard rail 7 — local-first), so it stays a fallback; a *named* provider we
# have no adapter for is not, and raises.
DEFAULT_PROVIDER = "mock"


class UnknownExtractionProviderError(ValueError):
    """`settings.extraction.provider` names an extraction adapter we don't have.

    Raised instead of substituting `mock`, whose `extract` returns a FIXTURE —
    see `get_extraction_adapter`.

    ``str(exc)`` deliberately does NOT echo the configured name: the message
    travels onto the ``extraction_failed`` Exception description, which every AP
    user reads, while only an admin owns the setting. The bounded raw value is
    on ``.provider`` for the admin-only `POST /api/organization/test-extraction`
    to name (`decisions.md` §29 — "say which name is wrong", to the admin).
    """

    def __init__(self, provider: str):
        self.provider = str(provider)[:_ADAPTER_KEY_ECHO_LIMIT]
        super().__init__(
            "The extraction provider configured for this organization is not a "
            "registered adapter. An administrator must correct it in Settings."
        )


def register_extraction_adapter(provider: str):
    """Decorator to register an extraction adapter class."""

    def wrapper(cls: type[ExtractionAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def _ensure_builtin_adapters() -> None:
    """Import every built-in adapter module so the registry is complete.

    Registration is a decorator side effect, so a lookup is only meaningful once
    the modules have been imported. Call sites used to each repeat their own
    import block, which meant the registry's contents depended on *which* caller
    you came through — tolerable while an unknown name silently became `mock`,
    but not once a miss raises. Imported lazily (inside the call, not at module
    scope) because each adapter module imports this one for the decorator.
    """
    import app.services.extraction_adapters.aws_textract  # noqa: F401
    import app.services.extraction_adapters.claude_vision  # noqa: F401
    import app.services.extraction_adapters.einvoice_adapter  # noqa: F401
    import app.services.extraction_adapters.mock_adapter  # noqa: F401
    import app.services.extraction_adapters.ollama  # noqa: F401
    import app.services.extraction_adapters.openai_vision  # noqa: F401


def get_extraction_adapter(extraction_config: dict) -> ExtractionAdapter:
    """Create the appropriate adapter based on org extraction config.

    Config shape:
        {
            "program_type": "platform" | "byok",
            "provider": "claude_vision" | "openai_vision" | "aws_textract" | "mock",
            ...provider-specific fields...
        }

    If program_type is "platform", platform-level keys from app settings are used.
    If "byok", customer-provided keys from org settings are used.

    **A named provider we don't have → `UnknownExtractionProviderError`.** This
    used to fall back to `mock`, which is not an inert stub: `MockExtractionAdapter.extract`
    returns a fabricated invoice ("Extracted Vendor Inc", 1500.00) at 0.95
    overall confidence — inside the band `decide_auto_approve` will approve
    touchlessly. So a BYOK org that typo'd `settings.extraction.provider` booked
    invented payables against real vendors instead of failing, and
    `POST /api/organization/test-extraction` answered "Connected to <typo>
    successfully" (`mock.test_connection` returns True) — the endpoint that
    exists to catch the misconfiguration confirmed it.

    `config.py::_validate_extraction_provider` already refuses a bad
    `FEOH_EXTRACTION_PROVIDER` at boot (`decisions.md` §26), but per-org names
    come from the tenant's DB settings, so there is no boot at which to check
    them — the refusal has to live here. Same call as `erp_adapters` /
    `payment_adapters` / `fx_adapters`; see `decisions.md` §29.

    An org that has configured NO provider at all still resolves to `mock` —
    that's the local-first default, not a misconfiguration.
    """
    _ensure_builtin_adapters()

    provider = (extraction_config.get("provider") or "").strip() or DEFAULT_PROVIDER

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        raise UnknownExtractionProviderError(provider)

    return adapter_cls(extraction_config)


def list_available_providers() -> list[str]:
    """Every registered provider name. Ensures the built-ins are imported first,
    so the "registered alternatives" an admin is shown can't be a partial list
    that depends on which module happened to be imported already."""
    _ensure_builtin_adapters()
    return sorted(_ADAPTER_REGISTRY.keys())
