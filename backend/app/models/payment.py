import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PaymentRun(Base, TimestampMixin):
    __tablename__ = "payment_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    status: Mapped[str] = mapped_column(String(30), default="draft")
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )


class PaymentSchedule(Base, TimestampMixin):
    __tablename__ = "payment_schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    discount_date: Mapped[date | None] = mapped_column(Date)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    payment_terms: Mapped[str | None] = mapped_column(String(100))


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    payment_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_runs.id")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    method: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    reference: Mapped[str | None] = mapped_column(String(255))
