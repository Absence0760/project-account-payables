"""Console email adapter — prints messages to stdout.

The default for local development. No secrets required, no network calls,
no costs. Use this to verify copy and links before flipping on SES.
"""

from __future__ import annotations

import logging

from app.services.email_adapters.base import EmailAdapter, EmailMessage
from app.services.email_adapters.dispatcher import register_email_adapter

logger = logging.getLogger(__name__)


@register_email_adapter("console")
class ConsoleAdapter(EmailAdapter):
    provider_name = "console"

    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "\n" + "=" * 72 + "\n"
            "EMAIL (console adapter — not actually sent)\n"
            f"To:      {message.to}\n"
            f"From:    {self.config.get('from_address', '')}\n"
            f"Subject: {message.subject}\n" + "-" * 72 + "\n"
            f"{message.body_text}\n" + "=" * 72
        )

    async def test_connection(self) -> bool:
        return True
