"""Base ERP adapter interface and shared data types."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


class ErpInvoiceStatus(enum.StrEnum):
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


@dataclass
class PoLinePayload:
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    total: Decimal | None = None
    gl_account: str | None = None


@dataclass
class PoPayload:
    """Normalized purchase order returned by `ErpAdapter.list_pos`.

    `status` is one of {"open", "closed", "cancelled"} — the same set
    `PurchaseOrder.status` accepts. Adapters that get something exotic
    from their ERP should normalize to one of these or default to
    "open" rather than passing the raw vendor string through.
    """

    po_number: str
    vendor_name: str | None = None
    total: Decimal = Decimal("0")
    status: str = "open"
    # Promised / expected delivery date from the ERP, when the upstream PO
    # record carries one. Maps straight onto ``PurchaseOrder.expected_delivery_date``
    # (the on-time-delivery vendor sub-score signal). None when the ERP doesn't
    # supply a promised date — real adapters must NOT fabricate one; the mock
    # adapter emits a deterministic value so local-first dev exercises the path.
    expected_delivery_date: date | None = None
    line_items: list[PoLinePayload] = field(default_factory=list)


@dataclass
class GLAccountPayload:
    """Normalized chart-of-accounts row returned by `ErpAdapter.list_gl_accounts`.

    `account_type` should normalize to one of {"asset", "liability",
    "equity", "revenue", "expense"} — the same vocabulary the rest of
    the app expects. Adapters can pass None when the upstream system
    doesn't classify the account; the API endpoint accepts that.
    `erp_account_id` is the upstream's canonical identifier so future
    pulls can detect renames.
    """

    code: str
    name: str
    account_type: str | None = None
    erp_account_id: str | None = None
    parent_code: str | None = None


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

    async def list_pos(self) -> list[PoPayload]:
        """Pull purchase orders from the ERP.

        Default implementation returns an empty list so adapters that
        don't yet support PO sync don't 500 the sync endpoint — the
        UI just reports "0 new POs". Adapters that *do* support it
        override this.
        """
        return []

    async def list_gl_accounts(self) -> list[GLAccountPayload]:
        """Pull the chart of accounts from the ERP.

        Same contract as `list_pos`: default returns an empty list so
        an adapter that doesn't expose a chart endpoint just leaves the
        local chart untouched. The Auto GL Coding pipeline relies on
        this — the prompt only constrains AI suggestions when the org
        has synced a real chart.
        """
        return []

    async def test_connection(self) -> bool:
        """Verify the ERP connection is working."""
        raise NotImplementedError
