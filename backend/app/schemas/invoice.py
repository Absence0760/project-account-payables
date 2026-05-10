from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class InvoiceStatus(StrEnum):
    new = "new"
    pending = "pending"
    ready_for_review = "ready_for_review"
    approved = "approved"
    rejected = "rejected"
    sending_to_erp = "sending_to_erp"
    sent_to_erp = "sent_to_erp"
    posted_in_erp = "posted_in_erp"
    payment_scheduled = "payment_scheduled"
    paid = "paid"
    done = "done"
    failed = "failed"


class InvoiceBase(BaseModel):
    vendor: str = Field(..., max_length=255)
    invoice_number: str = Field(..., max_length=100)
    amount: Decimal = Field(..., ge=0)
    currency: str = Field(default="USD", max_length=3)
    invoice_date: date | None = None
    received_date: date | None = None
    due_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=50)
    status: InvoiceStatus = InvoiceStatus.new
    po_number: str | None = Field(default=None, max_length=100)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_amount: Decimal | None = Field(default=None, ge=0)
    discount_amount: Decimal | None = Field(default=None, ge=0)
    shipping_amount: Decimal | None = Field(default=None, ge=0)
    remit_to_address: str | None = None
    bill_to_address: str | None = None
    vendor_address: str | None = None
    vendor_tax_id: str | None = Field(default=None, max_length=50)
    ship_to_address: str | None = None
    tax_rate: Decimal | None = Field(default=None, ge=0, le=100)
    payment_method: str | None = Field(default=None, max_length=50)
    reference_number: str | None = Field(default=None, max_length=100)
    description: str | None = None
    notes: str | None = None
    gl_account: str | None = Field(default=None, max_length=100)
    cost_center: str | None = Field(default=None, max_length=100)


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(BaseModel):
    vendor: str | None = Field(default=None, max_length=255)
    invoice_number: str | None = Field(default=None, max_length=100)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    invoice_date: date | None = None
    received_date: date | None = None
    due_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=50)
    status: InvoiceStatus | None = None
    po_number: str | None = None
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_amount: Decimal | None = Field(default=None, ge=0)
    discount_amount: Decimal | None = Field(default=None, ge=0)
    shipping_amount: Decimal | None = Field(default=None, ge=0)
    remit_to_address: str | None = None
    bill_to_address: str | None = None
    vendor_address: str | None = None
    vendor_tax_id: str | None = Field(default=None, max_length=50)
    ship_to_address: str | None = None
    tax_rate: Decimal | None = Field(default=None, ge=0, le=100)
    payment_method: str | None = Field(default=None, max_length=50)
    reference_number: str | None = Field(default=None, max_length=100)
    description: str | None = None
    notes: str | None = None
    gl_account: str | None = Field(default=None, max_length=100)
    cost_center: str | None = Field(default=None, max_length=100)


class InvoiceResponse(BaseModel):
    """Matches the frontend Invoice TypeScript interface exactly."""

    id: str
    correlation_id: str
    vendor: str
    invoice_number: str
    amount: float
    currency: str
    invoice_date: str | None
    received_date: str | None
    due_date: str | None
    payment_terms: str | None
    status: InvoiceStatus
    po_number: str
    subtotal: float | None
    tax_amount: float | None
    discount_amount: float | None
    shipping_amount: float | None
    remit_to_address: str | None
    bill_to_address: str | None
    vendor_address: str | None
    vendor_tax_id: str | None
    ship_to_address: str | None
    tax_rate: float | None
    payment_method: str | None
    reference_number: str | None
    description: str
    notes: str | None
    approval_date: str | None
    approved_by: str | None
    rejected_by: str | None
    assigned_to_id: str | None
    assigned_to: str | None
    gl_account: str | None
    cost_center: str | None
    created_at: str
    file_url: str | None
    warnings: list[dict] | None = None
    # Latest 2/3-way PO match result (status, variance, issues). Populated by
    # `services.invoice_warnings.refresh_warnings`. Null when the invoice has
    # no `po_number`. The invoice modal renders this as a PO Match panel.
    po_match: dict | None = None
    # Summary counts from the latest extraction's priors_metadata — feeds the
    # small "priors applied" indicator on the invoice-list row. Null when no
    # extraction ran or no priors fired.
    priors_summary: dict | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(cls, inv) -> "InvoiceResponse":
        return cls(
            id=str(inv.id),
            correlation_id=str(inv.correlation_id),
            vendor=inv.vendor_name,
            invoice_number=inv.invoice_number,
            amount=float(inv.amount),
            currency=inv.currency,
            invoice_date=inv.invoice_date.isoformat() if inv.invoice_date else None,
            received_date=inv.received_date.isoformat() if inv.received_date else None,
            due_date=inv.due_date.isoformat() if inv.due_date else None,
            payment_terms=inv.payment_terms,
            status=inv.status,
            po_number=inv.po_number or "",
            subtotal=float(inv.subtotal) if inv.subtotal is not None else None,
            tax_amount=float(inv.tax_amount) if inv.tax_amount is not None else None,
            discount_amount=float(inv.discount_amount) if inv.discount_amount is not None else None,
            shipping_amount=float(inv.shipping_amount) if inv.shipping_amount is not None else None,
            remit_to_address=inv.remit_to_address,
            bill_to_address=inv.bill_to_address,
            vendor_address=inv.vendor_address,
            vendor_tax_id=inv.vendor_tax_id,
            ship_to_address=inv.ship_to_address,
            tax_rate=float(inv.tax_rate) if inv.tax_rate is not None else None,
            payment_method=inv.payment_method,
            reference_number=inv.reference_number,
            description=inv.description or "",
            notes=inv.notes,
            approval_date=inv.approval_date.isoformat() if inv.approval_date else None,
            approved_by=inv.approved_by,
            rejected_by=inv.rejected_by,
            assigned_to_id=str(inv.assigned_to_id) if inv.assigned_to_id else None,
            assigned_to=inv.assigned_to,
            gl_account=inv.gl_account,
            cost_center=inv.cost_center,
            created_at=inv.created_at.isoformat() if inv.created_at else "",
            file_url=inv.file_url,
            warnings=inv.warnings,
            po_match=inv.po_match,
            priors_summary=_priors_summary(inv),
        )


def _priors_summary(inv) -> dict | None:
    """Flatten the latest extraction result's priors_metadata into counts."""
    results = getattr(inv, "extraction_results", None) or []
    if not results:
        return None
    latest = max(results, key=lambda r: r.created_at or 0)
    meta = latest.priors_metadata or {}
    cache = len(meta.get("vendor_cache_applied") or [])
    rag = len(meta.get("rag_neighbors") or [])
    if cache == 0 and rag == 0:
        return None
    return {"cache": cache, "rag": rag}


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int
    page: int
    page_size: int


class BulkDeleteRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1)


class BulkDeleteResponse(BaseModel):
    deleted: int
    skipped: list[str] = []


class BulkStatusRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1)
    status: InvoiceStatus


class BulkStatusResponse(BaseModel):
    updated: int
    skipped: list[str] = []


class BulkExportRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1)
    format: str = Field(default="csv", pattern=r"^(csv|json|xml)$")


class BulkRecodeGLRequest(BaseModel):
    """Bulk GL re-code filter. All filters are optional and AND-combined.

    `dry_run` defaults to True so an accidentally-fired request reports
    what would change without writing. `include_ai_fallback` is opt-in
    because it bills the org per invoice.
    """

    from_date: date | None = None
    to_date: date | None = None
    vendor_ids: list[str] = Field(default_factory=list)
    include_ai_fallback: bool = False
    dry_run: bool = True


class InvoiceLineItemResponse(BaseModel):
    id: str
    line_number: int | None
    item_code: str | None
    description: str | None
    quantity: float | None
    unit_price: float | None
    tax: float | None
    total: float | None
    gl_account: str | None

    model_config = {"from_attributes": True}
