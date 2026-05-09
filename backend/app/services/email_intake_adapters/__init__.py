"""Provider-specific parsers for inbound email webhooks.

Each adapter translates a provider's payload into a normalized
:class:`~app.services.email_intake.InboundEmail` object so the core
intake service stays provider-agnostic.

To add a new provider:
1. Create ``<provider>.py`` with a ``parse(body: bytes, headers: dict) ->
   InboundEmail | None`` function.
2. Register it in ``_REGISTRY`` below.

Providers currently supported:
- ``ses`` — AWS SES inbound email via SNS notification (JSON)
- ``mailgun`` — Mailgun inbound route webhook (form-data)
- ``generic`` — pass-through for a normalized JSON body (handy for tests
  and hand-rolled forwarders)
"""

from __future__ import annotations

from collections.abc import Callable

from app.services.email_intake import InboundEmail
from app.services.email_intake_adapters import generic as _generic
from app.services.email_intake_adapters import mailgun as _mailgun
from app.services.email_intake_adapters import ses as _ses

ParserFn = Callable[[bytes, dict[str, str]], "InboundEmail | None"]

_REGISTRY: dict[str, ParserFn] = {
    "ses": _ses.parse,
    "mailgun": _mailgun.parse,
    "generic": _generic.parse,
}


def get_parser(provider: str) -> ParserFn | None:
    return _REGISTRY.get(provider.lower())


def list_providers() -> list[str]:
    return list(_REGISTRY.keys())
