"""Notification dispatch — fan an invoice lifecycle event out to recipients.

Two effects per recipient, each gated by that recipient's per-user preference:

1. **in-app** — insert a `Notification` row into the *tenant* DB (the same
   session that owns the status transition, so it commits atomically with it).
2. **email** — build an `EmailMessage` and hand it to the configured email
   adapter (`console` by default — safe, no network, no secrets).

Both are **best-effort**. A failure here must never roll back or abort the
caller's status transition / audit write, so the whole dispatch is wrapped in a
guard and logs without PII (event type + notification id only, never the
recipient's email address or any invoice banking field).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.notification import NOTIFICATION_EVENT_TYPES, Notification
from app.services.notification_templates import InvoiceContext, RenderedNotification, render

logger = logging.getLogger(__name__)

# Default channel state when a user hasn't set an explicit preference.
_DEFAULT_CHANNELS = {"email": True, "in_app": True}


def resolve_prefs(notification_prefs: dict | None, event_type: str) -> dict:
    """Resolve the effective {email, in_app} channels for an event type.

    Missing event or missing channel key falls back to "on" — opt-out, not
    opt-in, so a brand-new user gets notifications by default.
    """
    prefs = notification_prefs or {}
    event_prefs = prefs.get(event_type) or {}
    return {
        "email": bool(event_prefs.get("email", _DEFAULT_CHANNELS["email"])),
        "in_app": bool(event_prefs.get("in_app", _DEFAULT_CHANNELS["in_app"])),
    }


async def _load_recipients(recipient_user_ids: list[uuid.UUID]) -> dict[uuid.UUID, object]:
    """Load control-plane User rows for the given ids, keyed by id.

    Uses its own control-plane session (the caller's `db` is tenant-scoped),
    mirroring `approval_chain.resolve_assignee`.
    """
    from app.database import control_session_factory
    from app.models.user import User

    unique_ids = list({uid for uid in recipient_user_ids if uid is not None})
    if not unique_ids:
        return {}

    async with control_session_factory() as ctrl_db:
        result = await ctrl_db.execute(select(User).where(User.id.in_(unique_ids)))
        users = result.scalars().all()
    return {u.id: u for u in users}


async def _resolve_org_slug(organization_id: uuid.UUID) -> str | None:
    """Look up an org's tenant slug (control plane). Used only to build the
    per-recipient email-approval links — returns None on any miss so the caller
    simply omits the links."""
    from app.database import control_session_factory
    from app.models.organization import Organization

    async with control_session_factory() as ctrl_db:
        result = await ctrl_db.execute(
            select(Organization.slug).where(Organization.id == organization_id)
        )
        return result.scalar_one_or_none()


async def _resolve_org_chat_config(organization_id: uuid.UUID) -> tuple[dict, str | None]:
    """Load an org's `settings.chat_notifications` config + its slug.

    Returns ``({}, None)`` on any miss so the caller can degrade to "no chat
    notification" without raising. The slug is used to build the (PII-free)
    deep link into the tenant app.
    """
    from app.database import control_session_factory
    from app.models.organization import Organization

    async with control_session_factory() as ctrl_db:
        result = await ctrl_db.execute(
            select(Organization.slug, Organization.settings).where(
                Organization.id == organization_id
            )
        )
        row = result.first()
    if row is None:
        return {}, None
    slug, org_settings = row
    chat_config = (org_settings or {}).get("chat_notifications") or {}
    if not isinstance(chat_config, dict):
        return {}, slug
    return chat_config, slug


async def resolve_role_user_ids(organization_id: uuid.UUID, role_name: str) -> list[uuid.UUID]:
    """Return the ids of active users in an org holding a given role.

    Used to fan a `paid` notification out to every AP manager. Runs against the
    control plane (where Users + Roles live); the role membership is the
    `user_roles` junction. System roles have `organization_id IS NULL`, so we
    match the role by name and scope the *users* by org instead.
    """
    from app.database import control_session_factory
    from app.models.user import Role, User, UserRole

    async with control_session_factory() as ctrl_db:
        result = await ctrl_db.execute(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                User.organization_id == organization_id,
                User.is_active == True,  # noqa: E712
                Role.name == role_name,
            )
        )
        return [row[0] for row in result.all()]


async def notify_event(
    db: AsyncSession,
    *,
    correlation_id: uuid.UUID,
    organization_id: uuid.UUID,
    event_type: str,
    entity_id: uuid.UUID | None,
    recipient_user_ids: list[uuid.UUID],
    invoice_ctx: InvoiceContext | None = None,
    rendered: RenderedNotification | None = None,
    actor_id: uuid.UUID | None = None,
    entity_type: str = "invoice",
) -> None:
    """Notify each recipient of `event_type`, gated by their preferences.

    Pass either ``invoice_ctx`` (the dispatcher renders the invoice template)
    or a pre-``rendered`` notification (for non-invoice events like contract
    renewals, which carry a different context). Exactly one is required.

    Never raises — all failures are swallowed and logged (without PII) so the
    caller's transaction is unaffected. The in-app rows are *added* to `db`
    (the caller's tenant session) but not committed here; the caller's existing
    commit flushes them alongside the status change + audit row.
    """
    if not settings.notifications_enabled:
        return

    if event_type not in NOTIFICATION_EVENT_TYPES:
        logger.warning("notify_event: unknown event_type=%s — skipping", event_type)
        return

    if rendered is None:
        if invoice_ctx is None:
            logger.warning("notify_event: no invoice_ctx or rendered for %s — skipping", event_type)
            return
        try:
            rendered = render(event_type, invoice_ctx)
        except Exception:  # noqa: BLE001 — never let a template bug break a transition
            logger.exception("notify_event: template render failed for event_type=%s", event_type)
            return

    try:
        users_by_id = await _load_recipients(recipient_user_ids)
    except Exception:  # noqa: BLE001
        logger.exception("notify_event: failed loading recipients for event_type=%s", event_type)
        return

    # Email approval: when an invoice is assigned for review and the feature is
    # configured, the reviewer's email gets per-recipient Approve/Reject links
    # (the token binds to *that* reviewer). Resolve the tenant slug once here;
    # the per-recipient token is built inside the loop. Best-effort — any miss
    # just omits the links.
    from app.models.notification import EVENT_INVOICE_ASSIGNED

    tenant_slug: str | None = None
    if (
        event_type == EVENT_INVOICE_ASSIGNED
        and entity_type == "invoice"
        and settings.email_action_signing_key
        and entity_id is not None
    ):
        try:
            tenant_slug = await _resolve_org_slug(organization_id)
        except Exception:  # noqa: BLE001
            logger.exception("notify_event: failed resolving tenant slug for action links")
            tenant_slug = None

    # De-dup recipients so a user who is both uploader and AP-manager gets one.
    for recipient_id in {uid for uid in recipient_user_ids if uid is not None}:
        user = users_by_id.get(recipient_id)
        if user is None:
            # Recipient no longer exists / wrong org — skip silently.
            continue
        if not getattr(user, "is_active", True):
            continue

        channels = resolve_prefs(getattr(user, "notification_prefs", None), event_type)

        if channels["in_app"]:
            db.add(
                Notification(
                    correlation_id=correlation_id,
                    organization_id=organization_id,
                    recipient_user_id=recipient_id,
                    event_type=event_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    title=rendered.title,
                    body=rendered.body_text,
                )
            )

        if channels["email"] and getattr(user, "email", None):
            email_text = rendered.body_text
            email_html = rendered.body_html
            if tenant_slug is not None:
                from app.services.email_action_token import build_email_action_links

                links = build_email_action_links(
                    api_base_url=settings.api_public_url,
                    tenant_slug=tenant_slug,
                    invoice_id=entity_id,
                    actor_id=recipient_id,
                    signing_key=settings.email_action_signing_key,
                    ttl_hours=settings.email_action_ttl_hours,
                )
                if links:
                    text_block, html_block = links
                    email_text = f"{email_text}\n\n{text_block}"
                    email_html = f"{email_html or ''}{html_block}"
            await _send_email_best_effort(
                user.email,
                rendered.title,
                email_text,
                email_html,
                event_type=event_type,
            )

    # Chat fan-out (Slack / Teams) — a single channel post per event, not
    # per-recipient. Approval-lifecycle events only; entirely best-effort and
    # self-guarded so a chat-send failure never breaks the caller's transition.
    if entity_type == "invoice" and invoice_ctx is not None:
        await _send_chat_best_effort(
            organization_id=organization_id,
            event_type=event_type,
            invoice_ctx=invoice_ctx,
            entity_id=entity_id,
        )


async def _send_email_best_effort(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None,
    *,
    event_type: str,
) -> None:
    """Send one email, swallowing + logging (PII-free) any failure."""
    from app.services.email_adapters import EmailMessage, get_email_adapter

    try:
        adapter = get_email_adapter()
        await adapter.send(
            EmailMessage(to=to, subject=subject, body_text=body_text, body_html=body_html)
        )
    except Exception:  # noqa: BLE001
        # PII rule: log the event type only — never the recipient address.
        logger.exception("notify_event: email send failed for event_type=%s", event_type)


def _chat_event_enabled(chat_config: dict, event_type: str) -> bool:
    """Whether the org wants chat notifications for this event.

    Master gate is `chat_config["enabled"]` (default off — chat is opt-in per
    org, unlike email/in-app which default on). The per-event `events` map is
    opt-out within that: a missing event key defaults to on once enabled.
    """
    if not chat_config.get("enabled"):
        return False
    events = chat_config.get("events")
    if not isinstance(events, dict):
        return True
    return bool(events.get(event_type, True))


async def _send_chat_best_effort(
    *,
    organization_id: uuid.UUID,
    event_type: str,
    invoice_ctx,
    entity_id: uuid.UUID | None,
) -> None:
    """Post one approval event to the org's chat channel (Slack/Teams).

    Best-effort + fully self-guarded: any failure (config load, adapter build,
    transport) is swallowed and logged PII-free so the caller's transaction is
    never affected. No-ops when the org hasn't enabled chat, the event isn't a
    chat-notifiable approval event, or the provider can't be resolved.
    """
    from app.services.chat_notification_adapters import (
        get_chat_notification_adapter,
        render_chat_message,
    )

    try:
        chat_config, slug = await _resolve_org_chat_config(organization_id)
    except Exception:  # noqa: BLE001
        logger.exception("notify_event: failed loading chat config for event_type=%s", event_type)
        return

    if not _chat_event_enabled(chat_config, event_type):
        return

    # Deep link into the tenant app (no secrets / PII). Best-effort — omitted
    # when the slug or entity is missing.
    link: str | None = None
    if slug and entity_id is not None:
        try:
            base = settings.tenant_url_template.format(slug=slug).rstrip("/")
            link = f"{base}/invoices/{entity_id}"
        except Exception:  # noqa: BLE001 — a bad template must not break dispatch
            link = None

    message = render_chat_message(
        event_type,
        invoice_number=getattr(invoice_ctx, "invoice_number", "") or "",
        vendor_name=getattr(invoice_ctx, "vendor_name", "") or "",
        amount=getattr(invoice_ctx, "amount", None),
        currency=getattr(invoice_ctx, "currency", None) or "USD",
        link=link,
    )
    if message is None:
        # Not a chat-notifiable event (e.g. chat_message / contract_renewal).
        return

    try:
        adapter = get_chat_notification_adapter(chat_config)
        await adapter.send(message)
    except Exception:  # noqa: BLE001
        # PII rule: log the event type only — never the webhook URL or amount.
        logger.exception("notify_event: chat send failed for event_type=%s", event_type)
