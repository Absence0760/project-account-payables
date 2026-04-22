"""In-memory audit-log sink — local dev default, and the easiest stub for tests.

Every batch `ship()`ed gets appended to `received` so tests can assert on
what flowed through. Module-level (not instance) because the dispatcher
rebuilds adapter instances per tick.
"""

from __future__ import annotations

import logging

from app.services.audit_shipping.base import AuditLogRow, AuditShippingAdapter
from app.services.audit_shipping.dispatcher import register_audit_shipping_adapter

logger = logging.getLogger(__name__)


# Module-level capture — dispatcher rebuilds adapter instances each tick
# so storing on the instance would lose data between ticks.
received: list[AuditLogRow] = []


def reset() -> None:
    """Clear the captured rows. Call from test setup."""
    received.clear()


@register_audit_shipping_adapter("mock")
class MockAdapter(AuditShippingAdapter):
    provider_name = "mock"

    async def ship(self, rows: list[AuditLogRow]) -> None:
        received.extend(rows)
        logger.debug("[audit-shipping:mock] captured %d row(s)", len(rows))

    async def test_connection(self) -> bool:
        return True
