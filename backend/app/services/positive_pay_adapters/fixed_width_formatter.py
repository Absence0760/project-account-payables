"""Fixed-width Positive Pay formatter.

Many legacy bank cash-management portals accept only a flat, column-aligned
fixed-width file (one record per line, no delimiters). This formatter renders
that layout deterministically: every field occupies a fixed character width,
left-justified text / right-justified amounts, space-padded, truncated if a
value overruns its column.

Column widths (characters) — documented here so the layout is a contract, not
an accident:

  check_issue record (total 80):
    check_number     10  | payee            40  | amount           14
    issue_date        8 (YYYYMMDD) | account_number    8

  ach_authorization record (total 80):
    vendor_name      40  | routing_number    9  | account_number   17
    status           14

Amount is rendered as zero-padded cents (no decimal point): ``$1,234.56`` →
``00000000123456`` in 14 chars. Exact — derived from the :class:`Decimal`, never
a float. An empty item list renders an empty string (no header — fixed-width
files are headerless).
"""

from __future__ import annotations

from decimal import Decimal

from app.services.positive_pay_adapters.base import (
    AchAuthorizationItem,
    CheckIssueItem,
    FormatterContext,
    PositivePayFormatter,
)
from app.services.positive_pay_adapters.dispatcher import register_positive_pay_formatter

# check_issue column widths
_W_CHECK_NUMBER = 10
_W_PAYEE = 40
_W_AMOUNT = 14
_W_ISSUE_DATE = 8
_W_ACCOUNT = 8

# ach_authorization column widths
_W_VENDOR = 40
_W_ROUTING = 9
_W_ACH_ACCOUNT = 17
_W_STATUS = 14


def _ltext(value: str, width: int) -> str:
    """Left-justify text into ``width`` chars, space-padded, truncated."""
    return value[:width].ljust(width)


def _amount_cents(amount: Decimal, width: int) -> str:
    """Render an amount as zero-padded cents in ``width`` chars (no sign, no
    decimal point). Quantized to 2 places so fractional cents can't leak."""
    cents = int((amount.quantize(Decimal("0.01")) * 100).to_integral_value())
    return str(abs(cents)).rjust(width, "0")[:width]


@register_positive_pay_formatter("fixed_width")
class FixedWidthPositivePayFormatter(PositivePayFormatter):
    format_name = "fixed_width"
    file_extension = "txt"
    content_type = "text/plain"

    def format_check_issue(self, items: list[CheckIssueItem], ctx: FormatterContext) -> str:
        lines: list[str] = []
        for it in items:
            line = (
                _ltext(it.check_number, _W_CHECK_NUMBER)
                + _ltext(it.payee, _W_PAYEE)
                + _amount_cents(it.amount, _W_AMOUNT)
                + it.issue_date.strftime("%Y%m%d")[:_W_ISSUE_DATE].ljust(_W_ISSUE_DATE)
                + _ltext(it.account_number, _W_ACCOUNT)
            )
            lines.append(line)
        return "\r\n".join(lines) + ("\r\n" if lines else "")

    def format_ach_authorization(
        self, items: list[AchAuthorizationItem], ctx: FormatterContext
    ) -> str:
        lines: list[str] = []
        for it in items:
            line = (
                _ltext(it.vendor_name, _W_VENDOR)
                + _ltext(it.routing_number, _W_ROUTING)
                + _ltext(it.account_number, _W_ACH_ACCOUNT)
                + _ltext(it.status, _W_STATUS)
            )
            lines.append(line)
        return "\r\n".join(lines) + ("\r\n" if lines else "")
