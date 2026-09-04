"""Base Positive Pay formatter interface.

A *formatter* renders a Positive Pay export into the byte-for-byte layout one
bank expects. Positive Pay is a treasury fraud control: we hand the bank the
list of cheques we *issued* (a ``check_issue`` file) or the accounts authorized
to debit us (an ``ach_authorization`` file). Every bank has its own file layout,
so the formatter is a pluggable adapter — exactly like the payment processors in
``app.services.payment_adapters``.

Formatters are **pure renderers**: they take already-built dataclasses and a
:class:`FormatterContext` and return a ``str``. They never touch the DB, never
log, and never see anything but the data handed to them. The rendered output
legitimately contains full account / routing numbers — that *is* the file's
purpose, and it is stored in MinIO, not in any DB display column. The PII
invariant (no full account number in logs / audit / error bodies) is the
*caller's* responsibility; the formatter is the one place a full number is
deliberately emitted.

Money is :class:`~decimal.Decimal` (never float). See
``backend/docs/positive-pay.md``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal


class PositivePayFieldOverflow(ValueError):
    """A value cannot be rendered into its fixed-width column without changing
    what it means.

    Raised only for *identifiers and money* — a check number, an account or
    routing number, an amount. A Positive Pay file exists so the bank can
    refuse anything that doesn't match what we issued; a silently-truncated
    check number or a mis-scaled amount inverts that control, making the bank
    refuse a cheque we really wrote (or match one we didn't). Failing the
    export is recoverable, a confidently-wrong fraud-control file is not.

    Descriptive text (payee, vendor name, status) is NOT covered: truncating a
    long name is the documented, intended behaviour of a fixed-width layout,
    and the bank matches on the identifiers rather than the name.
    """

    def __init__(self, field: str, width: int) -> None:
        # PII-free by construction: names the column and its width, never the
        # offending value — these carry full account / routing numbers.
        super().__init__(
            f"{field} does not fit its {width}-character column; "
            "refusing to emit a truncated Positive Pay record"
        )
        self.field = field
        self.width = width


@dataclass
class CheckIssueItem:
    """One issued cheque, the unit of a ``check_issue`` Positive Pay file.

    ``account_number`` is the originating (drawee) account the cheque is drawn
    on — the same for every line in a file, carried per-item so a formatter can
    emit it on each row if the bank wants it. It is a full account number: it
    belongs only in the rendered file, never in a log or the DB row.
    """

    check_number: str
    payee: str
    amount: Decimal
    issue_date: datetime.date
    account_number: str


@dataclass
class AchAuthorizationItem:
    """One authorized ACH debit relationship, the unit of an
    ``ach_authorization`` file. Carries the vendor's full routing + account
    number — again, file-only, never logged."""

    vendor_name: str
    routing_number: str
    account_number: str
    status: str


@dataclass
class FormatterContext:
    """File-level context shared by every line of a rendered file — the
    originating company + account, the file date, and the currency. Built by
    the router from the org settings; the originating ``account_number`` is a
    full number used only inside the rendered file."""

    company_name: str
    account_number: str
    file_date: datetime.date
    currency: str


class PositivePayFormatter:
    """Base class for a bank-specific Positive Pay file formatter.

    Subclasses register with ``@register_positive_pay_formatter("<name>")`` and
    set ``format_name`` / ``file_extension`` / ``content_type``. They MUST be
    stateless and deterministic — the same items + context always render the
    same bytes, so the content hash is stable.
    """

    format_name: str = "base"
    file_extension: str = "txt"
    content_type: str = "text/plain"

    def format_check_issue(self, items: list[CheckIssueItem], ctx: FormatterContext) -> str:
        """Render a check-issue file. Deterministic; pure."""
        raise NotImplementedError

    def format_ach_authorization(
        self, items: list[AchAuthorizationItem], ctx: FormatterContext
    ) -> str:
        """Render an ACH debit-authorization file. Deterministic; pure."""
        raise NotImplementedError
