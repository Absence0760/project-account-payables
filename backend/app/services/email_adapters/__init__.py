"""Transactional email adapters — unified interface for provider plugins."""

# Import adapters so they register themselves with the dispatcher.
from app.services.email_adapters import console_adapter as _console  # noqa: F401
from app.services.email_adapters import ses_adapter as _ses  # noqa: F401
from app.services.email_adapters import smtp_adapter as _smtp  # noqa: F401
from app.services.email_adapters.base import EmailAdapter, EmailMessage
from app.services.email_adapters.dispatcher import (
    get_email_adapter,
    list_available_providers,
    register_email_adapter,
)
from app.services.email_adapters.email_catalogue import (
    DEFAULT_LOCALE,
    SUPPORTED_EMAIL_LOCALES,
    is_supported_locale,
    normalize_locale,
    translate,
)

__all__ = [
    "DEFAULT_LOCALE",
    "SUPPORTED_EMAIL_LOCALES",
    "EmailAdapter",
    "EmailMessage",
    "get_email_adapter",
    "is_supported_locale",
    "list_available_providers",
    "normalize_locale",
    "register_email_adapter",
    "translate",
]
