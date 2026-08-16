"""ERP adapter package — unified interface for all ERP integrations."""

from app.services.erp_adapters.base import (
    ErpAdapter,
    ErpInvoiceStatus,
    ErpPostResult,
    InvoicePayload,
    LineItemPayload,
)
from app.services.erp_adapters.dispatcher import (
    UnknownErpAdapterError,
    get_erp_adapter,
    list_available_adapters,
)

__all__ = [
    "ErpAdapter",
    "ErpInvoiceStatus",
    "ErpPostResult",
    "InvoicePayload",
    "LineItemPayload",
    "UnknownErpAdapterError",
    "get_erp_adapter",
    "list_available_adapters",
]
