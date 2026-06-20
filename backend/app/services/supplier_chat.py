"""Supplier-chat service — lazy thread creation, templates, serialization, and
the notification / supplier-email helpers shared by both the AP and portal
surfaces.

The two routers (``api/invoices.py`` AP side, ``api/portal.py`` portal side)
own the HTTP shape; everything reusable across them lives here. See
``backend/docs/supplier-chat.md``.

PII rule: nothing in this module logs (or audits) a message body, vendor email,
mention names, or filenames — ids, roles, and booleans only.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.invoice import Invoice
from app.models.notification import EVENT_CHAT_MESSAGE
from app.models.organization import Organization
from app.models.supplier_chat import (
    ChatThreadStatus,
    SupplierChatMessage,
    SupplierChatThread,
)
from app.models.vendor import Vendor
from app.services.notification_dispatch import notify_event, resolve_role_user_ids
from app.services.notification_templates import InvoiceContext

logger = logging.getLogger(__name__)

# Static, in-code canned templates surfaced via GET /api/invoices/chat/templates
# (not config, not secrets). The endpoint is the source of truth.
CHAT_TEMPLATES: list[dict] = [
    {
        "key": "missing_po",
        "label": "Missing PO number",
        "body": (
            "We're unable to match this invoice to a purchase order. "
            "Could you provide the PO number?"
        ),
    },
    {
        "key": "amount_mismatch",
        "label": "Amount mismatch",
        "body": (
            "The invoice total doesn't match our records. "
            "Could you confirm the amount and send a corrected copy if needed?"
        ),
    },
    {
        "key": "payment_status",
        "label": "Payment status",
        "body": (
            "Your invoice is approved and scheduled for payment. We'll notify you once it's paid."
        ),
    },
]

_TEMPLATE_KEYS = {t["key"] for t in CHAT_TEMPLATES}


def chat_enabled(org: Organization | None) -> bool:
    """Org feature-flag read (off-safe default True, local-first).

    Follows the ``invoice_warnings`` / ``po_matching.require_inspection``
    precedent: ``(org.settings or {}).get("supplier_chat", {}).get("enabled", True)``.
    Default True so the feature works out of the box on a fresh local tenant; an
    org can opt out.
    """
    settings_dict = (getattr(org, "settings", None) or {}) if org is not None else {}
    return bool((settings_dict.get("supplier_chat") or {}).get("enabled", True))


def is_valid_template_key(key: str | None) -> bool:
    return key is None or key in _TEMPLATE_KEYS


async def get_or_create_thread(db: AsyncSession, invoice: Invoice) -> SupplierChatThread:
    """Return the invoice's chat thread, creating it (flushed) on first post.

    The ``uq_supplier_chat_thread_invoice`` unique index guarantees at most one
    thread per invoice. Runs inside the caller's tenant txn.
    """
    thread = (
        await db.execute(
            select(SupplierChatThread).where(SupplierChatThread.invoice_id == invoice.id)
        )
    ).scalar_one_or_none()
    if thread is not None:
        return thread

    thread = SupplierChatThread(
        invoice_id=invoice.id,
        status=ChatThreadStatus.open,
        organization_id=invoice.organization_id,
        entity_id=invoice.entity_id,
    )
    db.add(thread)
    await db.flush()
    return thread


async def get_thread(db: AsyncSession, invoice_id: uuid.UUID) -> SupplierChatThread | None:
    return (
        await db.execute(
            select(SupplierChatThread).where(SupplierChatThread.invoice_id == invoice_id)
        )
    ).scalar_one_or_none()


async def list_messages(db: AsyncSession, thread_id: uuid.UUID) -> list[SupplierChatMessage]:
    return list(
        (
            await db.execute(
                select(SupplierChatMessage)
                .where(SupplierChatMessage.thread_id == thread_id)
                .order_by(SupplierChatMessage.created_at)
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Notifications + supplier email
# ---------------------------------------------------------------------------


def _invoice_ctx(invoice: Invoice, *, note: str | None = None) -> InvoiceContext:
    """Build the PII-free notification context for a chat message."""
    return InvoiceContext(
        invoice_number=invoice.invoice_number or str(invoice.id),
        vendor_name=invoice.vendor_name or "",
        note=note,
    )


async def notify_supplier_post(
    db: AsyncSession,
    *,
    invoice: Invoice,
) -> None:
    """A supplier posted (portal) — notify the org's AP managers.

    ``notify_event`` is gated internally by ``settings.notifications_enabled``
    and only ever reaches control-plane Users (never a VendorUser). Called
    before the caller's commit so the in-app rows ride the same tenant txn.
    """
    recipients = await resolve_role_user_ids(invoice.organization_id, "ap_manager")
    await notify_event(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        event_type=EVENT_CHAT_MESSAGE,
        entity_id=invoice.id,
        recipient_user_ids=recipients,
        invoice_ctx=_invoice_ctx(invoice, note="from supplier"),
        actor_id=None,
    )


async def notify_ap_mentions(
    db: AsyncSession,
    *,
    invoice: Invoice,
    mention_user_ids: list[uuid.UUID],
    actor_id: uuid.UUID | None,
) -> None:
    """An AP user posted — notify the mentioned teammates (poster excluded)."""
    recipients = [uid for uid in mention_user_ids if uid != actor_id]
    if not recipients:
        return
    await notify_event(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        event_type=EVENT_CHAT_MESSAGE,
        entity_id=invoice.id,
        recipient_user_ids=recipients,
        invoice_ctx=_invoice_ctx(invoice, note="you were mentioned"),
        actor_id=actor_id,
    )


async def notify_supplier_of_ap_message(
    db: AsyncSession,
    *,
    org: Organization,
    invoice: Invoice,
    vendor: Vendor | None,
) -> None:
    """Send the supplier a direct, best-effort email with a portal chat link
    when an AP user posts.

    Modeled on ``card_issuance._send_vendor_card_email``. Skips silently when
    there's no vendor email or no tenant URL template. PII-free subject/body
    (invoice number + vendor name only — never the message text). This direct
    path is NOT auto-gated, so we check ``notifications_enabled`` ourselves.
    """
    if not settings.notifications_enabled:
        return
    if vendor is None or not vendor.email:
        return

    base = (settings.tenant_url_template or "").replace("{slug}", org.slug)
    if not base:
        return
    link = f"{base.rstrip('/')}/portal/invoices/{invoice.id}/chat"

    inv_ref = invoice.invoice_number or str(invoice.id)
    org_name = (org.settings or {}).get("company", {}).get("name") or org.name
    subject = f"{org_name}: new message on invoice {inv_ref}"
    body_text = (
        f"Hi {vendor.name},\n\n"
        f"{org_name} posted a new message on invoice {inv_ref}.\n\n"
        f"View the conversation and reply:\n  {link}\n"
    )
    body_html = (
        f"<p>Hi {vendor.name},</p>"
        f"<p><strong>{org_name}</strong> posted a new message on invoice "
        f"<code>{inv_ref}</code>.</p>"
        f'<p><a href="{link}">View the conversation and reply</a></p>'
    )

    from app.services.branding import get_brand_context
    from app.services.email_adapters import EmailMessage, get_email_adapter

    try:
        adapter = get_email_adapter()
        await adapter.send(
            EmailMessage(
                to=vendor.email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                brand=get_brand_context(org.settings),
            )
        )
    except Exception as exc:  # noqa: BLE001
        # PII rule: log invoice id + error only — never the vendor email.
        logger.warning(
            "[supplier_chat] supplier email send failed for invoice %s: %s",
            invoice.id,
            exc,
        )
