"""Microsoft Teams chat-notification adapter — posts to an incoming-webhook URL.

Teams incoming webhooks accept a legacy **MessageCard** JSON body — a different
shape from Slack's ``{text, blocks}``. We send a MessageCard with a summary, a
title, a ``facts`` section (vendor / status / amount), and an optional
``potentialAction`` "OpenUri" button for the deep link.

Same fail-closed contract as the Slack adapter: the per-org
``Organization.settings.chat_notifications.webhook_url`` is the credential, so an
absent URL is a no-op + PII-free warning, never an exception. No platform-level
hardcoded fallback.
"""

from __future__ import annotations

import logging

import httpx

from app.services.chat_notification_adapters.base import ChatMessage, ChatNotificationAdapter
from app.services.chat_notification_adapters.dispatcher import register_chat_notification_adapter

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10.0)


@register_chat_notification_adapter("teams")
class TeamsChatNotificationAdapter(ChatNotificationAdapter):
    provider_name = "teams"

    def __init__(self, config: dict):
        super().__init__(config)
        self.webhook_url: str = (config or {}).get("webhook_url") or ""

    def build_body(self, message: ChatMessage) -> dict:
        """Shape a ChatMessage into a Teams MessageCard JSON body.

        Pure (no network) so tests can assert the exact shape.
        """
        facts = [
            {"name": "Invoice", "value": message.invoice_number},
            {"name": "Vendor", "value": message.vendor_name},
            {"name": "Status", "value": message.status},
        ]
        amount = message.amount_str()
        if amount:
            facts.append({"name": "Amount", "value": amount})

        body: dict = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": message.title,
            "themeColor": "0076D7",
            "title": message.title,
            "sections": [{"facts": facts, "markdown": True}],
        }
        if message.link:
            body["potentialAction"] = [
                {
                    "@type": "OpenUri",
                    "name": "View invoice",
                    "targets": [{"os": "default", "uri": message.link}],
                }
            ]
        return body

    async def send(self, message: ChatMessage) -> None:
        if not self.webhook_url:
            # Fail closed — PII-free: event type only, never the webhook URL.
            logger.warning(
                "teams chat-notification: no webhook_url configured — skipping event=%s",
                message.event_type,
            )
            return
        body = self.build_body(message)
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(self.webhook_url, json=body)
        response.raise_for_status()

    async def test_connection(self) -> bool:
        return bool(self.webhook_url)
