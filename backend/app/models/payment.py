import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PaymentRun(Base, TimestampMixin):
    __tablename__ = "payment_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )


class PaymentSchedule(Base, TimestampMixin):
    __tablename__ = "payment_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    discount_date: Mapped[date | None] = mapped_column(Date)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    payment_terms: Mapped[str | None] = mapped_column(String(100))


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
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
    # Which adapter handled this payment (`mock`, `modern_treasury`, ...).
    # Populated when the row is submitted to a processor. Null for pre-
    # adapter rows backfilled from the legacy fake-execute path.
    provider: Mapped[str | None] = mapped_column(String(50))
    # Processor's identifier — used to look up the row when a webhook lands.
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), index=True)
    # Set on `failed` / `cancelled`. Free-form so we can preserve the
    # processor's exact error message for debugging.
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # Lifecycle timestamps. `submitted_at` = sent to processor; `completed_at`
    # = terminal status reported. Lets us compute settlement latency for
    # ops dashboards without parsing audit logs.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
