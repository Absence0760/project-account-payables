"""Positive Pay formatter dispatcher — selects the bank file layout by name.

Mirrors ``app.services.payment_adapters.dispatcher``: a module-level registry,
a ``@register_positive_pay_formatter("<name>")`` decorator, and a
``get_positive_pay_formatter`` lookup that falls back to the ``csv`` formatter
for an unknown / missing name (keeps a misconfigured ``bank_format`` from
500-ing file generation).
"""

from __future__ import annotations

from app.services.positive_pay_adapters.base import PositivePayFormatter

_REGISTRY: dict[str, type[PositivePayFormatter]] = {}

# The always-available fallback. Every install ships the CSV formatter, so an
# unknown bank_format degrades to a sane, readable file rather than failing.
DEFAULT_FORMAT = "csv"


def register_positive_pay_formatter(name: str):
    """Decorator to register a formatter class under ``name``."""

    def wrapper(cls: type[PositivePayFormatter]):
        _REGISTRY[name] = cls
        return cls

    return wrapper


def get_positive_pay_formatter(name_or_config: str | dict | None) -> PositivePayFormatter:
    """Build the formatter for ``name_or_config``.

    Accepts a bare format name (``"csv"``), a config dict carrying a
    ``bank_format`` / ``format`` key, or ``None``. An unknown or missing name
    falls back to the ``csv`` formatter so a misconfigured bank format never
    breaks file generation.
    """
    if isinstance(name_or_config, dict):
        name = name_or_config.get("bank_format") or name_or_config.get("format") or DEFAULT_FORMAT
    else:
        name = name_or_config or DEFAULT_FORMAT

    formatter_cls = _REGISTRY.get(name) or _REGISTRY.get(DEFAULT_FORMAT)
    if formatter_cls is None:
        raise ValueError(
            f"No positive-pay formatter registered for '{name}' and no '{DEFAULT_FORMAT}' fallback"
        )
    return formatter_cls()


def list_available_formats() -> list[str]:
    return sorted(_REGISTRY.keys())
