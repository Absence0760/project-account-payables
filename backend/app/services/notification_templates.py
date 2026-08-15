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


# Map the canonical event_type strings to the catalogue key prefix. The
# catalogue (app/services/email_adapters/email_catalogue.py) owns the per-locale
# copy; this module stays the thin event → (title, body) renderer.
_EVENT_TO_KEY = {
    EVENT_INVOICE_ASSIGNED: "invoice_assigned",
    EVENT_INVOICE_APPROVED: "invoice_approved",
    EVENT_INVOICE_REJECTED: "invoice_rejected",
    EVENT_INVOICE_PAID: "invoice_paid",
    EVENT_CHAT_MESSAGE: "chat_message",
}


def render(
    event_type: str, ctx: InvoiceContext, *, locale: str | None = None
) -> RenderedNotification:
    """Render the notification for `event_type`. Raises on unknown events.

    ``locale`` selects the email-copy language (account-level DB ``locale``
    preference of the recipient). Defaults to ``None`` → English, which is what
    the in-app notification center always uses (the in-app row is NOT localized
    by the recipient's email locale — see ``docs/notifications.md``). The deep
    links, money amount, invoice number, and vendor name are interpolated as
    placeholders, so they stay identical across locales — only the copy changes.
    """
    # Imported lazily to keep this pure module free of the adapter package at
    # import time (avoids a cycle: adapters never import templates).
    from app.services.email_adapters.email_catalogue import translate

    key = _EVENT_TO_KEY.get(event_type)
    if key is None:
        raise ValueError(f"No notification template for event type '{event_type}'")

    ref = f"Invoice {ctx.invoice_number} ({ctx.vendor_name})"
    money = _money(ctx)

    title = translate(f"notif.{key}.title", locale, ref=ref)
    body = translate(f"notif.{key}.body", locale, ref=ref, money=money)

    if event_type == EVENT_INVOICE_REJECTED and ctx.reason:
        body += translate("notif.invoice_rejected.reason", locale, reason=ctx.reason)
    elif event_type == EVENT_CHAT_MESSAGE and ctx.note:
        # NEVER put the raw message body into title/body. ctx.note may carry at
        # most a short author label (e.g. "from supplier") — no message text.
        body += translate("notif.chat_message.note", locale, note=ctx.note)

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


def render_cash_shortfall(
    *,
    period: str,
    closing: Decimal,
    threshold: Decimal,
    shortfall: Decimal,
    currency: str,
    breach_count: int,
) -> RenderedNotification:
    """Render the projected-cash-shortfall notification (PII-free).

    Like `render_contract_renewal`, this carries its own context rather than an
    invoice one, so the dispatcher passes it through pre-rendered. Every figure
    formats straight off the `Decimal` — money never round-trips through float
    on its way into an email.

    The numbers are org-level aggregates (a projected closing balance, the
    configured minimum), never per-vendor or per-invoice detail, so nothing
    PII-bearing reaches the message.
    """
    more = (
        ""
        if breach_count <= 1
        else f" {breach_count} periods in the forecast close below the minimum."
    )
    title = f"Projected cash shortfall in {period}"
    body = (
        f"Your projected cash balance closes at {currency} {closing:,.2f} in {period} — "
        f"{currency} {shortfall:,.2f} below your {currency} {threshold:,.2f} minimum."
        f"{more} Review the cash-flow forecast before scheduling more payments."
    )
    return RenderedNotification(title=title, body_text=body, body_html=f"<p>{body}</p>")
