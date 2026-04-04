import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class InvoiceStatus(str, enum.Enum):
    new = "new"
    pending = "pending"
    ready_for_review = "ready_for_review"
    approved = "approved"
    rejected = "rejected"
    sending_to_erp = "sending_to_erp"
    sent_to_erp = "sent_to_erp"
    failed = "failed"


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, index=True
    )
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
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
    notes: Mapped[str | None] = mapped_column(Text)
    approval_date: Mapped[date | None] = mapped_column(Date)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    gl_account: Mapped[str | None] = mapped_column(String(100))
    cost_center: Mapped[str | None] = mapped_column(String(100))
    file_url: Mapped[str | None] = mapped_column(String(1024))
    file_key: Mapped[str | None] = mapped_column(String(512))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id")
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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    raw_result: Mapped[dict | None] = mapped_column(JSONB)

    invoice: Mapped[Invoice] = relationship(back_populates="extraction_results")
