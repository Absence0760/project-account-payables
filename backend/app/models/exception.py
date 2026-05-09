import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Exception(Base, TimestampMixin):
    __tablename__ = "exceptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    exception_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Types: duplicate, po_mismatch, fraud_flag, extraction_failed,
    #        unverified_vendor, review_rejected, amount_exceeded, missing_data
    severity: Mapped[str] = mapped_column(String(20), default="warning")  # error, warning, info
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(30), default="open"
    )  # open, resolved, escalated, dismissed
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(255))  # user name
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[str | None] = mapped_column(String(255))  # user name for routing

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
