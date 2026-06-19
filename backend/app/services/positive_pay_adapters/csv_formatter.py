"""CSV Positive Pay formatter — the local-first default bank layout.

Renders RFC-4180 CSV via the stdlib :mod:`csv` module (so quoting / escaping is
correct for payees with commas or quotes). Amounts are plain decimal strings
(``str(Decimal)`` — exact, never float-rounded); dates are ISO ``YYYY-MM-DD``.

Header rows are fixed so the output is deterministic and diffable:

  * check_issue:        ``check_number,payee,amount,issue_date,account_number``
  * ach_authorization:  ``vendor_name,routing_number,account_number,status``

An empty item list renders the header row alone (a valid, if empty, file).
"""

from __future__ import annotations

import csv
import io

from app.services.positive_pay_adapters.base import (
    AchAuthorizationItem,
    CheckIssueItem,
    FormatterContext,
    PositivePayFormatter,
)
from app.services.positive_pay_adapters.dispatcher import register_positive_pay_formatter

_CHECK_ISSUE_HEADER = ["check_number", "payee", "amount", "issue_date", "account_number"]
_ACH_HEADER = ["vendor_name", "routing_number", "account_number", "status"]


@register_positive_pay_formatter("csv")
class CsvPositivePayFormatter(PositivePayFormatter):
    format_name = "csv"
    file_extension = "csv"
    content_type = "text/csv"

    def format_check_issue(self, items: list[CheckIssueItem], ctx: FormatterContext) -> str:
        buf = io.StringIO()
        # \r\n line terminator + QUOTE_MINIMAL = RFC-4180.
        writer = csv.writer(buf, lineterminator="\r\n")
        writer.writerow(_CHECK_ISSUE_HEADER)
        for it in items:
            writer.writerow(
                [
                    it.check_number,
                    it.payee,
                    str(it.amount),
                    it.issue_date.isoformat(),
                    it.account_number,
                ]
            )
        return buf.getvalue()

    def format_ach_authorization(
        self, items: list[AchAuthorizationItem], ctx: FormatterContext
    ) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        writer.writerow(_ACH_HEADER)
        for it in items:
            writer.writerow([it.vendor_name, it.routing_number, it.account_number, it.status])
        return buf.getvalue()
