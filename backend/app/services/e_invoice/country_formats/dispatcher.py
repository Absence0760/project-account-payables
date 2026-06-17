"""Country e-invoice format dispatcher — registry + lookup.

Mirrors the adapter dispatchers (``peppol_adapters.dispatcher`` et al.): a
decorator registers a :class:`CountryEInvoiceFormat` subclass under its
``format_code``, and the export route resolves the instance from the
``?format=`` query parameter. Unlike the networked adapters there is no config
or ``mock`` fallback — these are pure generators, so an unknown format is an
explicit, PII-free error the route surfaces as a 400.
"""

from __future__ import annotations

from app.services.e_invoice.country_formats.base import CountryEInvoiceFormat

_FORMAT_REGISTRY: dict[str, CountryEInvoiceFormat] = {}


def register_country_format(code: str):
    """Decorator: register a :class:`CountryEInvoiceFormat` subclass.

    The class is instantiated once at import time (the formats are stateless)
    and stored under ``code``.
    """

    def wrapper(cls: type[CountryEInvoiceFormat]) -> type[CountryEInvoiceFormat]:
        _FORMAT_REGISTRY[code] = cls()
        return cls

    return wrapper


def get_country_format(code: str) -> CountryEInvoiceFormat | None:
    """Return the registered format instance for ``code``, or ``None``."""
    return _FORMAT_REGISTRY.get(code)


def list_country_formats() -> list[str]:
    """Return the registered format codes, sorted (stable for API listing)."""
    return sorted(_FORMAT_REGISTRY.keys())
