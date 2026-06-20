"""Mock chat-notification adapter — the local-first default.

No network, no Slack/Teams credential, no cost. Records the message it would
have sent (on the instance + a process-wide log the tests can assert against)
and logs a one-line PII-safe summary. This is what `pnpm dev` uses, so a fresh
clone exercises the full chat fan-out path without any real webhook.
"""

from __future__ import annotations

import logging

from app.services.chat_notification_adapters.base import ChatMessage, ChatNotificationAdapter
from app.services.chat_notification_adapters.dispatcher import register_chat_notification_adapter

logger = logging.getLogger(__name__)

# Process-wide capture of what the mock adapter "sent" — handy for tests and
# local debugging. Cleared by tests that assert on it.
SENT: list[ChatMessage] = []


@register_chat_notification_adapter("mock")
class MockChatNotificationAdapter(ChatNotificationAdapter):
    provider_name = "mock"

    async def send(self, message: ChatMessage) -> None:
        SENT.append(message)
        logger.info(
            "CHAT (mock adapter — not actually sent) event=%s invoice=%s",
            message.event_type,
            message.invoice_number,
        )

    async def test_connection(self) -> bool:
        return True
