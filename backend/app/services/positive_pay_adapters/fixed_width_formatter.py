"""Fixed-width Positive Pay formatter.

Many legacy bank cash-management portals accept only a flat, column-aligned
fixed-width file (one record per line, no delimiters). This formatter renders
that layout deterministically: every field occupies a fixed character width,
left-justified text / right-justified amounts, space-padded, truncated if a
value overruns its column.

Column widths (characters) — documented here so the layout is a contract, not
an accident:

  check_issue record (total 89):
    check_number     10  | payee            40  | amount           14
    issue_date        8 (YYYYMMDD) | account_number   17

The drawee ``account_number`` column is 17, not 8. It carries a FULL bank
account number straight from ``settings.payments.check_account_number``, and a
US account alone runs 8-12 digits — an 8-char column could not hold one, so
every rendered file silently carried a truncated account and pointed the record
at an account that does not exist. 17 matches the ``ach_authorization``
account column in this same layout, which holds the same kind of value.

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
    PositivePayFieldOverflow,
    PositivePayFormatter,
)
from app.services.positive_pay_adapters.dispatcher import register_positive_pay_formatter

# check_issue column widths
_W_CHECK_NUMBER = 10
_W_PAYEE = 40
_W_AMOUNT = 14
_W_ISSUE_DATE = 8
_W_ACCOUNT = 17

# ach_authorization column widths
_W_VENDOR = 40
_W_ROUTING = 9
_W_ACH_ACCOUNT = 17
_W_STATUS = 14


def _ltext(value: str, width: int) -> str:
    """Left-justify DESCRIPTIVE text into ``width`` chars, space-padded.

    Truncation here is intended: a payee or vendor name that overruns its
    column is cosmetic, and the bank matches on the identifiers rendered by
    :func:`_lid`. Never use this for a check / account / routing number.
    """
    return value[:width].ljust(width)


def _lid(value: str, width: int, field: str) -> str:
    """Left-justify an IDENTIFIER into ``width`` chars, space-padded.

    Unlike :func:`_ltext` this refuses to truncate. A check number silently cut
    to its first ``width`` characters produces a record the bank matches
    against nothing, so it rejects a cheque we genuinely issued; an account or
    routing number cut the same way points the record at a different account.
    Both invert the control the file exists to provide, so an overrun is a
    hard failure rather than a quietly wrong line.
    """
    if len(value) > width:
        raise PositivePayFieldOverflow(field, width)
    return value.ljust(width)


def _amount_cents(amount: Decimal, width: int, field: str = "amount") -> str:
    """Render an amount as zero-padded cents in ``width`` chars (no sign, no
    decimal point). Quantized to 2 places so fractional cents can't leak.

    An amount too large for its column is refused, never trimmed. The old
    ``[:width]`` slice kept the HIGH-order digits and dropped the low-order
    ones, so an overrunning figure didn't merely lose precision — it was
    rescaled by a factor of ten per dropped digit (``123456`` cents into a
    4-char column read as ``1234``, i.e. $12.34 instead of $1,234.56) and the
    bank would clear a cheque for the wrong amount.
    """
    cents = int((amount.quantize(Decimal("0.01")) * 100).to_integral_value())
    rendered = str(abs(cents))
    if len(rendered) > width:
        raise PositivePayFieldOverflow(field, width)
    return rendered.rjust(width, "0")


@register_positive_pay_formatter("fixed_width")
class FixedWidthPositivePayFormatter(PositivePayFormatter):
    format_name = "fixed_width"
    file_extension = "txt"
    content_type = "text/plain"

    def format_check_issue(self, items: list[CheckIssueItem], ctx: FormatterContext) -> str:
        lines: list[str] = []
        for it in items:
            line = (
                _lid(it.check_number, _W_CHECK_NUMBER, "check_number")
                + _ltext(it.payee, _W_PAYEE)
                + _amount_cents(it.amount, _W_AMOUNT)
                + it.issue_date.strftime("%Y%m%d")[:_W_ISSUE_DATE].ljust(_W_ISSUE_DATE)
                + _lid(it.account_number, _W_ACCOUNT, "account_number")
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
                + _lid(it.routing_number, _W_ROUTING, "routing_number")
                + _lid(it.account_number, _W_ACH_ACCOUNT, "account_number")
                + _ltext(it.status, _W_STATUS)
            )
            lines.append(line)
        return "\r\n".join(lines) + ("\r\n" if lines else "")
