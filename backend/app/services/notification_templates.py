"""Notification templates — pure event -> (subject, in-app title/body, email).

Every template renders from a small, deliberately PII-free context: invoice
number, vendor name, amount, currency, and status. **Never** bank details, tax
IDs, full vendor addresses, or payment-method numbers — these strings land in
emails and log-adjacent in-app rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.notification import (
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
    else:
        raise ValueError(f"No notification template for event type '{event_type}'")

    body_html = f"<p>{body}</p>"
    return RenderedNotification(title=title, body_text=body, body_html=body_html)
