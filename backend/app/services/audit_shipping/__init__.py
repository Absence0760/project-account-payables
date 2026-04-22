"""Audit-log shipping adapter package.

A background loop (`services.audit_log_shipper`) reads unshipped rows from
every tenant's `audit_log` table and fans them out to one or more WORM-
compliant sinks via the adapters here.

To add a new adapter:

    @register_audit_shipping_adapter("splunk")
    class SplunkAdapter(AuditShippingAdapter):
        async def ship(self, rows): ...
        async def test_connection(self) -> bool: ...

Then add its name to `AP_AUDIT_SHIPPING_PROVIDERS` (comma-separated).
"""

# Importing adapter modules registers them with the dispatcher. Keep
# these imports at the bottom so the registry is populated by the time
# any consumer calls `get_audit_shipping_adapter`.
from app.services.audit_shipping import (
    cloudwatch_adapter,  # noqa: F401,E402
    mock_adapter,  # noqa: F401,E402
    s3_objectlock_adapter,  # noqa: F401,E402
)
from app.services.audit_shipping.base import AuditLogRow, AuditShippingAdapter
from app.services.audit_shipping.dispatcher import (
    get_audit_shipping_adapter,
    get_audit_shipping_adapters,
    list_available_providers,
    register_audit_shipping_adapter,
)

__all__ = [
    "AuditLogRow",
    "AuditShippingAdapter",
    "get_audit_shipping_adapter",
    "get_audit_shipping_adapters",
    "list_available_providers",
    "register_audit_shipping_adapter",
]
