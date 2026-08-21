"""Positive Pay formatter dispatcher — selects the bank file layout by name.

Mirrors ``app.services.payment_adapters.dispatcher``: a module-level registry, a
``@register_positive_pay_formatter("<name>")`` decorator, and a
``get_positive_pay_formatter`` lookup that resolves a MISSING name to ``csv``
(the local-first default every install ships) and **refuses a named layout it
has no formatter for**.

Falling back to ``csv`` for a named-but-unknown layout was the same shape
``decisions.md`` §29 removed from the payment / ERP / FX dispatchers and §36
from sanctions: the fallback is not an inert stub, so the misconfiguration
becomes a confident wrong answer instead of an error. Here it is a **fraud
control that silently stops working** — one typo in ``bank_format`` (``"wells"``
for a real registered layout) rendered a CSV body, stored it, stamped the
``PositivePayFile`` row and the audit trail with the *requested* name, filed the
``(run, bank_format)`` idempotency slot under it, and reported 201. The bank
then cannot parse the file, so either every cheque in the run is refused or —
worse — Positive Pay is simply not in force and an altered cheque clears with
nothing to match it against. Nothing anywhere said so.
"""

from __future__ import annotations

from app.services.positive_pay_adapters.base import PositivePayFormatter

_REGISTRY: dict[str, type[PositivePayFormatter]] = {}

# The layout used when the caller names none. Every install ships the CSV
# formatter, so an org that has never picked a bank layout still gets a file —
# the local-first default (guard rail 7), NOT a catch-all for a bad name.
DEFAULT_FORMAT = "csv"

# How much of a caller-supplied format name may be echoed back in an error. The
# column it comes from is `String(30)`; bounding it keeps an absurd value out of
# a log line or an HTTP body.
_FORMAT_NAME_ECHO_LIMIT = 30


class UnknownPositivePayFormatError(ValueError):
    """``bank_format`` names a bank layout we have no formatter for.

    Raised instead of silently rendering the ``csv`` layout under the requested
    name — see the module docstring for why that is a fraud-control failure
    rather than a cosmetic one. The API layer turns it into a 422 naming the bad
    value and the registered alternatives.
    """

    def __init__(self, name: str):
        # A caller-supplied format name, not PII — but bound it anyway so an
        # oversized value can't bloat a log line or a response body.
        self.name = str(name)[:_FORMAT_NAME_ECHO_LIMIT]
        super().__init__(
            f"No positive-pay formatter registered for '{self.name}'. "
            f"Registered formats: {', '.join(list_available_formats())}."
        )


def register_positive_pay_formatter(name: str):
    """Decorator to register a formatter class under ``name``."""

    def wrapper(cls: type[PositivePayFormatter]):
        _REGISTRY[name] = cls
        return cls

    return wrapper


def get_positive_pay_formatter(name_or_config: str | dict | None) -> PositivePayFormatter:
    """Build the formatter for ``name_or_config``.

    Accepts a bare format name (``"csv"``), a config dict carrying a
    ``bank_format`` / ``format`` key, or ``None``.

    **No name → ``csv``** (the local-first default). **A named layout we have no
    formatter for → :class:`UnknownPositivePayFormatError`.**
    """
    if isinstance(name_or_config, dict):
        name = name_or_config.get("bank_format") or name_or_config.get("format") or DEFAULT_FORMAT
    else:
        name = name_or_config or DEFAULT_FORMAT

    formatter_cls = _REGISTRY.get(name)
    if formatter_cls is None:
        raise UnknownPositivePayFormatError(name)
    return formatter_cls()


def list_available_formats() -> list[str]:
    return sorted(_REGISTRY.keys())
