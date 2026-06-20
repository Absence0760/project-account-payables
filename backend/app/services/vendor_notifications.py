"""Vendor-portal notification preferences + email fan-out.

Mirrors `services/notification_dispatch.py` (which serves control-plane
`User`s) but for `VendorUser`s — the suppliers who own a given invoice. A
vendor controls, per portal user, whether they get **emailed** when one of
their own invoices is paid or rejected. Vendors have no in-app notification
center, so only the `email` channel exists here.

Two public surfaces:

- `prefs_to_response` / `apply_pref_update` — pure mapping between the stored
  `VendorUser.notification_prefs` JSONB blob (keyed by the same event_type
  strings the rest of the system uses, e.g. `invoice_paid`) and the
  vendor-friendly API shape (`email_on_payment` / `email_on_rejection`).
- `notify_vendor_of_invoice_event` — best-effort: load the invoice's vendor's
  active portal users, and email each one whose pref allows it. Never raises;
  a failure must never break the invoice status transition that triggered it.

PII rule: emails reuse the shared PII-free invoice templates
(`notification_templates.render`), and failures log the event type only —
never a recipient address or any banking field.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.notification import EVENT_INVOICE_PAID, EVENT_INVOICE_REJECTED
from app.models.vendor_user import VendorUser
from app.services.notification_dispatch import resolve_prefs
from app.services.notification_templates import InvoiceContext, render

logger = logging.getLogger(__name__)

# The invoice lifecycle events a vendor portal user can opt out of, paired with
# the vendor-friendly API field name. Only these two events fan out to vendors.
_EVENT_TO_FIELD = {
    EVENT_INVOICE_PAID: "email_on_payment",
    EVENT_INVOICE_REJECTED: "email_on_rejection",
}


def prefs_to_response(notification_prefs: dict | None) -> dict[str, bool]:
    """Resolve the stored JSONB blob into the vendor-friendly response shape.

    Opt-out, not opt-in: a missing event / channel defaults to **on**, exactly
    like `resolve_prefs` does for control-plane users.
    """
    return {
        field: resolve_prefs(notification_prefs, event)["email"]
        for event, field in _EVENT_TO_FIELD.items()
    }


def apply_pref_update(notification_prefs: dict | None, update: dict[str, bool]) -> dict:
    """Return a new prefs blob with the given friendly-field updates applied.

    `update` carries only the fields the caller actually set (others left
    unchanged). Stores under the canonical event_type keys so the dispatch path
    and the control-plane `resolve_prefs` helper read the same shape.
    """
    prefs = dict(notification_prefs or {})
    field_to_event = {field: event for event, field in _EVENT_TO_FIELD.items()}
    for field, value in update.items():
        event = field_to_event.get(field)
        if event is None:
            continue
        event_prefs = dict(prefs.get(event) or {})
        event_prefs["email"] = bool(value)
        prefs[event] = event_prefs
    return prefs


async def notify_vendor_of_invoice_event(
    db: AsyncSession,
    *,
    event_type: str,
    invoice,
    reason: str | None = None,
) -> None:
    """Email the invoice's vendor's portal users of `event_type`, pref-gated.

    Best-effort and self-contained: swallows + logs (PII-free) every failure so
    the caller's status transition / audit write is never affected. Only fires
    for the two vendor-facing events; anything else is a silent no-op.
    """
    if not settings.notifications_enabled:
        return
    if event_type not in _EVENT_TO_FIELD:
        return

    vendor_id = getattr(invoice, "vendor_id", None)
    if vendor_id is None:
        return

    try:
        result = await db.execute(
            select(
                VendorUser.id,
                VendorUser.email,
                VendorUser.notification_prefs,
                VendorUser.locale,
            )
            .where(VendorUser.vendor_id == vendor_id)
            .where(VendorUser.is_active.is_(True))
        )
        recipients = result.all()
    except Exception:  # noqa: BLE001 — recipient lookup must not break the transition
        logger.exception(
            "notify_vendor_of_invoice_event: failed loading portal users for event_type=%s",
            event_type,
        )
        return

    if not recipients:
        return

    # Resolve the tenant brand once for the vendor emails (best-effort).
    from app.services.notification_dispatch import _resolve_org_brand

    org_id = getattr(invoice, "organization_id", None)
    brand = await _resolve_org_brand(org_id) if org_id is not None else None

    ctx = InvoiceContext(
        invoice_number=getattr(invoice, "invoice_number", "") or "",
        vendor_name=getattr(invoice, "vendor_name", "") or "",
        amount=getattr(invoice, "amount", None),
        currency=getattr(invoice, "currency", None) or "USD",
        reason=reason,
    )

    for _vu_id, email, prefs, locale in recipients:
        if not email:
            continue
        channels = resolve_prefs(prefs, event_type)
        if not channels["email"]:
            continue
        # Localize each supplier-user's email to their own account-level locale
        # preference (DB `VendorUser.locale`); NULL → English. Render per
        # recipient so two portal users of the same vendor can each get their
        # chosen language. A template bug must never break the transition.
        try:
            rendered = render(event_type, ctx, locale=locale)
        except Exception:  # noqa: BLE001
            logger.exception(
                "notify_vendor_of_invoice_event: template render failed for event_type=%s",
                event_type,
            )
            continue
        await _send_vendor_email_best_effort(
            email,
            rendered.title,
            rendered.body_text,
            rendered.body_html,
            event_type=event_type,
            brand=brand,
        )


async def _send_vendor_email_best_effort(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None,
    *,
    event_type: str,
    brand=None,
) -> None:
    """Send one email, swallowing + logging (PII-free) any failure."""
    from app.services.email_adapters import EmailMessage, get_email_adapter

    try:
        adapter = get_email_adapter()
        await adapter.send(
            EmailMessage(
                to=to,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                brand=brand,
            )
        )
    except Exception:  # noqa: BLE001 — PII rule: never log the address
        logger.exception(
            "notify_vendor_of_invoice_event: email send failed for event_type=%s", event_type
        )
