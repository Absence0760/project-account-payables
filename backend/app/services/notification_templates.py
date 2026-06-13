"""Notification templates — pure event -> (subject, in-app title/body, email).

Every template renders from a small, deliberately PII-free context: invoice
number, vendor name, amount, currency, and status. **Never** bank details, tax
IDs, full vendor addresses, or payment-method numbers — these strings land in
emails and log-adjacent in-app rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.notification import (
    EVENT_CHAT_MESSAGE,
    EVENT_INVOICE_APPROVED,
    EVENT_INVOICE_ASSIGNED,
    EVENT_INVOICE_PAID,
    EVENT_INVOICE_REJECTED,
)


@dataclass(frozen=True)
class InvoiceContext:
    """The minimal, PII-free invoice fields a template may reference."""

    invoice_number: str
    vendor_name: str
    amount: Decimal | None = None
    currency: str = "USD"
    reason: str | None = None  # e.g. rejection reason — free text, no PII expected
    # Short, PII-free chat snippet (e.g. an author label like "from supplier").
    # NEVER the raw message body — see EVENT_CHAT_MESSAGE render branch.
    note: str | None = None


@dataclass(frozen=True)
class RenderedNotification:
    title: str  # in-app title + email subject
    body_text: str  # in-app body + email plaintext
    body_html: str | None = None


def _money(ctx: InvoiceContext) -> str:
    if ctx.amount is None:
        return ""
    # Render exactly from the Decimal — never coerce currency through float.
    return f" for {ctx.currency} {ctx.amount:,.2f}"


def render(event_type: str, ctx: InvoiceContext) -> RenderedNotification:
    """Render the notification for `event_type`. Raises on unknown events."""
    ref = f"Invoice {ctx.invoice_number} ({ctx.vendor_name})"
    money = _money(ctx)

    if event_type == EVENT_INVOICE_ASSIGNED:
        title = f"{ref} assigned to you for review"
        body = f"{ref}{money} has been assigned to you for review."
    elif event_type == EVENT_INVOICE_APPROVED:
        title = f"{ref} was approved"
        body = f"{ref}{money} has been approved."
    elif event_type == EVENT_INVOICE_REJECTED:
        title = f"{ref} was rejected"
        body = f"{ref}{money} was rejected."
        if ctx.reason:
            body += f" Reason: {ctx.reason}"
    elif event_type == EVENT_INVOICE_PAID:
        title = f"{ref} was paid"
        body = f"{ref}{money} has been marked paid."
    elif event_type == EVENT_CHAT_MESSAGE:
        # NEVER put the raw message body into title/body. ctx.note may carry at
        # most a short author label (e.g. "from supplier") — no message text.
        title = f"New message on {ref}"
        body = f"A new message was posted on {ref}."
        if ctx.note:
            body += f" ({ctx.note})"
    else:
        raise ValueError(f"No notification template for event type '{event_type}'")

    body_html = f"<p>{body}</p>"
    return RenderedNotification(title=title, body_text=body, body_html=body_html)


def render_contract_renewal(
    *,
    contract_number: str,
    vendor_name: str | None,
    end_date: date,
    days_until: int,
) -> RenderedNotification:
    """Render the contract-renewal-due notification (PII-free).

    Built separately from `render` because it carries a contract context, not
    an invoice one — the dispatcher passes the result through as a pre-rendered
    notification.
    """
    vendor = f" with {vendor_name}" if vendor_name else ""
    when = "today" if days_until <= 0 else f"in {days_until} day{'s' if days_until != 1 else ''}"
    title = f"Contract {contract_number} expires {when}"
    body = (
        f"Contract {contract_number}{vendor} expires on {end_date.isoformat()} ({when}). "
        "Review it for renewal."
    )
    return RenderedNotification(title=title, body_text=body, body_html=f"<p>{body}</p>")
