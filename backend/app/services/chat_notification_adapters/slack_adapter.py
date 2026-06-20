"""Slack chat-notification adapter — posts to an incoming-webhook URL.

Slack incoming webhooks accept a JSON body of the shape
``{"text": "...", "blocks": [...]}``. We send a short ``text`` fallback plus a
single ``section`` block with the (PII-free) details and an optional link.

The webhook URL is a per-org value carried on
``Organization.settings.chat_notifications.webhook_url``. It is the credential,
so the adapter **fails closed** when it's absent: no-op + a PII-free warning,
never an exception (a chat misconfiguration must not break an invoice
transition). No platform-level hardcoded fallback exists.
"""

from __future__ import annotations

import logging

import httpx

from app.services.chat_notification_adapters.base import ChatMessage, ChatNotificationAdapter
from app.services.chat_notification_adapters.dispatcher import register_chat_notification_adapter

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10.0)


@register_chat_notification_adapter("slack")
class SlackChatNotificationAdapter(ChatNotificationAdapter):
    provider_name = "slack"

    def __init__(self, config: dict):
        super().__init__(config)
        self.webhook_url: str = (config or {}).get("webhook_url") or ""

    def build_body(self, message: ChatMessage) -> dict:
        """Shape a ChatMessage into Slack's incoming-webhook JSON body.

        Pure (no network) so tests can assert the exact shape.
        """
        lines = [f"*Vendor:* {message.vendor_name}", f"*Status:* {message.status}"]
        amount = message.amount_str()
        if amount:
            lines.append(f"*Amount:* {amount}")
        section_text = f"*{message.title}*\n" + "\n".join(lines)
        if message.link:
            section_text += f"\n<{message.link}|View invoice>"

        return {
            "text": message.title,  # plain fallback (notifications / no-block clients)
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": section_text},
                }
            ],
        }

    async def send(self, message: ChatMessage) -> None:
        if not self.webhook_url:
            # Fail closed — PII-free: event type only, never the webhook URL.
            logger.warning(
                "slack chat-notification: no webhook_url configured — skipping event=%s",
                message.event_type,
            )
            return
        body = self.build_body(message)
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(self.webhook_url, json=body)
        response.raise_for_status()

    async def test_connection(self) -> bool:
        return bool(self.webhook_url)
