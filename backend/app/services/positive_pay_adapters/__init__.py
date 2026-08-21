"""Positive Pay formatter adapters — pluggable bank file layouts.

Importing the concrete formatter modules here runs their
``@register_positive_pay_formatter`` decorators so the registry is populated as
soon as the package is imported (same self-registration pattern as
``app.services.payment_adapters``).
"""

# Import formatters so they self-register with the dispatcher.
from app.services.positive_pay_adapters import csv_formatter as _csv  # noqa: F401
from app.services.positive_pay_adapters import fixed_width_formatter as _fixed_width  # noqa: F401
from app.services.positive_pay_adapters.base import (
    AchAuthorizationItem,
    CheckIssueItem,
    FormatterContext,
    PositivePayFormatter,
)
from app.services.positive_pay_adapters.dispatcher import (
    UnknownPositivePayFormatError,
    get_positive_pay_formatter,
    list_available_formats,
    register_positive_pay_formatter,
)

__all__ = [
    "AchAuthorizationItem",
    "CheckIssueItem",
    "FormatterContext",
    "PositivePayFormatter",
    "UnknownPositivePayFormatError",
    "get_positive_pay_formatter",
    "list_available_formats",
    "register_positive_pay_formatter",
]
