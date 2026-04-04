import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
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
    failed = "failed"
    sent_to_erp = "sent_to_erp"


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, native_enum=False, length=30),
        default=InvoiceStatus.new,
    )
    po_number: Mapped[str | None] = mapped_column(String(100))
    file_url: Mapped[str | None] = mapped_column(String(1024))
    file_key: Mapped[str | None] = mapped_column(String(512))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id")
    )

    organization: Mapped["Organization"] = relationship(back_populates="invoices")  # noqa: F821
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
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

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
