"""Base email adapter interface and shared types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmailMessage:
    """An outbound transactional email. Plaintext + optional HTML body."""

    to: str
    subject: str
    body_text: str
    body_html: str | None = None


class EmailAdapter:
    """Base class for transactional email providers."""

    provider_name: str = "base"

    def __init__(self, config: dict):
        self.config = config

    async def send(self, message: EmailMessage) -> None:
        raise NotImplementedError

    async def test_connection(self) -> bool:
        raise NotImplementedError
