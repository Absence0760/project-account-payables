"""ERP adapter package — unified interface for all ERP integrations."""

from app.services.erp_adapters.base import (
    ErpAdapter,
    ErpInvoiceStatus,
    ErpPostResult,
    InvoicePayload,
    LineItemPayload,
)
from app.services.erp_adapters.dispatcher import get_erp_adapter

__all__ = [
    "ErpAdapter",
    "ErpInvoiceStatus",
    "ErpPostResult",
    "InvoicePayload",
    "LineItemPayload",
    "get_erp_adapter",
]
