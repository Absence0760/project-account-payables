"""Outbound chat-notification adapters — Slack / Teams approval fan-out.

A pluggable family mirroring `email_adapters/`: a decorator registry, a
per-org-config-aware factory (`get_chat_notification_adapter`), and a local-first
`mock` default so `pnpm dev` needs no real Slack/Teams webhook. Wired into the
notification chokepoint (`services/notification_dispatch.notify_event`) as a
best-effort fan-out — a chat-send failure never breaks an invoice transition.
See backend/docs/notifications.md § Chat notifications (Slack/Teams).
"""

# Import adapters so they register themselves with the dispatcher.
from app.services.chat_notification_adapters import mock_adapter as _mock  # noqa: F401
from app.services.chat_notification_adapters import slack_adapter as _slack  # noqa: F401
from app.services.chat_notification_adapters import teams_adapter as _teams  # noqa: F401
from app.services.chat_notification_adapters.base import (
    CHAT_EVENT_TYPES,
    ChatMessage,
    ChatNotificationAdapter,
    render_chat_message,
)
from app.services.chat_notification_adapters.dispatcher import (
    get_chat_notification_adapter,
    list_available_providers,
    register_chat_notification_adapter,
)

__all__ = [
    "CHAT_EVENT_TYPES",
    "ChatMessage",
    "ChatNotificationAdapter",
    "get_chat_notification_adapter",
    "list_available_providers",
    "register_chat_notification_adapter",
    "render_chat_message",
]
