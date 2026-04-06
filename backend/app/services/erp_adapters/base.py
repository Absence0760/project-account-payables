"""Base ERP adapter interface and shared data types."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


class ErpInvoiceStatus(str, enum.Enum):
    draft = "draft"
    open = "open"
    partially_paid = "partially_paid"
    paid = "paid"
    cancelled = "cancelled"
    unknown = "unknown"


@dataclass
class LineItemPayload:
    line_number: int
    item_code: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    gl_account: str | None = None


@dataclass
class InvoicePayload:
    """Normalized invoice data sent to every ERP adapter."""

    correlation_id: str
    invoice_number: str
    vendor_name: str
    amount: Decimal
    currency: str = "USD"
    vendor_tax_id: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    po_number: str | None = None
    description: str | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    tax_rate: Decimal | None = None
    discount_amount: Decimal | None = None
    shipping_amount: Decimal | None = None
    gl_account: str | None = None
    cost_center: str | None = None
    payment_terms: str | None = None
    payment_method: str | None = None
    bill_to_address: str | None = None
    remit_to_address: str | None = None
    vendor_address: str | None = None
    line_items: list[LineItemPayload] = field(default_factory=list)


@dataclass
class ErpPostResult:
    success: bool
    erp_document_id: str | None = None
    erp_document_number: str | None = None
    message: str | None = None
    raw_response: dict | None = None


class ErpAdapter:
    """Base class for ERP integrations. Subclass and implement all methods."""

    erp_type: str = "base"

    def __init__(self, config: dict):
        self.config = config

    async def post_invoice(self, payload: InvoicePayload) -> ErpPostResult:
        """Send an invoice to the ERP. Returns the ERP document ID."""
        raise NotImplementedError

    async def get_invoice_status(self, erp_document_id: str) -> ErpInvoiceStatus:
        """Poll the ERP for the current status of a posted invoice."""
        raise NotImplementedError

    async def void_invoice(self, erp_document_id: str) -> bool:
        """Request cancellation of a posted invoice."""
        raise NotImplementedError

    async def test_connection(self) -> bool:
        """Verify the ERP connection is working."""
        raise NotImplementedError
