import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class InvoiceStatus(enum.StrEnum):
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


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, index=True
    )
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    # Materialized conversion of `amount` into the org's reporting (base)
    # currency. Populated by `services.currency_conversion.materialize_reporting_amount`
    # whenever the invoice is created / mutated, locking the FX rate AT THAT
    # TIME so historical rollups never silently recompute with today's rate
    # (project invariant: money is exact + auditable). NULL until first
    # materialized; when `currency == reporting_currency` the rate is 1 and
    # `reporting_amount == amount`. See backend/docs/multi-currency.md.
    reporting_currency: Mapped[str | None] = mapped_column(String(3))
    reporting_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    reporting_fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    reporting_fx_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    received_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    payment_terms: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, native_enum=False, length=30),
        default=InvoiceStatus.new,
    )
    po_number: Mapped[str | None] = mapped_column(String(100))
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    shipping_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    remit_to_address: Mapped[str | None] = mapped_column(Text)
    bill_to_address: Mapped[str | None] = mapped_column(Text)
    vendor_address: Mapped[str | None] = mapped_column(Text)
    vendor_tax_id: Mapped[str | None] = mapped_column(String(50))
    ship_to_address: Mapped[str | None] = mapped_column(Text)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    payment_method: Mapped[str | None] = mapped_column(String(50))
    reference_number: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    approval_date: Mapped[date | None] = mapped_column(Date)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    rejected_by: Mapped[str | None] = mapped_column(String(255))
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assigned_to: Mapped[str | None] = mapped_column(String(255))
    gl_account: Mapped[str | None] = mapped_column(String(100))
    cost_center: Mapped[str | None] = mapped_column(String(100))
    file_url: Mapped[str | None] = mapped_column(String(1024))
    file_key: Mapped[str | None] = mapped_column(String(512))
    warnings: Mapped[list | None] = mapped_column(JSONB)
    # Latest PO match result. Shape: {status, match_type, po_id, po_number,
    # po_total, invoice_amount, variance, variance_pct, within_tolerance,
    # has_gr, gr_id, issues}. Populated by `services.invoice_warnings.refresh_warnings`
    # whenever the invoice changes. NULL when the invoice has no `po_number`.
    po_match: Mapped[dict | None] = mapped_column(JSONB)
    # Free-form per-invoice metadata bag. Currently holds the cached
    # audit-log summary under `meta["audit_summary"]`:
    #   {"text": str, "confidence_context": str|None,
    #    "source_fingerprint": {"count": int, "last_at": str|None},
    #    "generated_at": str, "model": str}
    # The summary is regenerated lazily (services.audit_summary) whenever the
    # audit-log fingerprint for this invoice's correlation_id changes — see
    # backend/docs/audit-summary.md. No PII / banking data is stored here.
    meta: Mapped[dict | None] = mapped_column(JSONB)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id")
    )

    __table_args__ = (
        # Idempotency backstop for the supplier-portal PO flip
        # (POST /api/portal/purchase-orders/{id}/flip). The marker
        # `po-flip:<po_id>` is unique per tenant, so this partial unique index
        # makes a concurrent double-flip of the same PO impossible to persist —
        # the second INSERT raises IntegrityError, which the handler catches and
        # turns into the idempotent short-circuit response. The app-level
        # existing-invoice check stays as the fast path. Partial predicate keeps
        # it from ever constraining ordinary invoices' `reference_number`.
        Index(
            "uq_invoice_po_flip_ref",
            "reference_number",
            unique=True,
            postgresql_where=text("reference_number LIKE 'po-flip:%'"),
        ),
    )

    vendor_rel: Mapped["Vendor | None"] = relationship(back_populates="invoices")  # noqa: F821
    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    extraction_results: Mapped[list["InvoiceExtractionResult"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceLineItem(Base, TimestampMixin):
    __tablename__ = "invoice_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    line_number: Mapped[int | None] = mapped_column(Integer)
    item_code: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    gl_account: Mapped[str | None] = mapped_column(String(100))

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")


class InvoiceExtractionResult(Base, TimestampMixin):
    __tablename__ = "invoice_extraction_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    raw_result: Mapped[dict | None] = mapped_column(JSONB)
    # Tracks what the priors pipeline did for this extraction so the UI can
    # show the reviewer which past invoices / vendor-cache entries shaped
    # the output. Shape:
    #   {
    #     "vendor_cache_applied": ["currency", "tax_rate"],
    #     "rag_neighbors": [{"invoice_id": "...", "similarity": 0.87, ...}]
    #   }
    priors_metadata: Mapped[dict | None] = mapped_column(JSONB)

    invoice: Mapped[Invoice] = relationship(back_populates="extraction_results")
