"""Extraction usage tracking for billing."""

import uuid

from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ExtractionUsage(Base, TimestampMixin):
    __tablename__ = "extraction_usage"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    program_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "platform" or "byok"
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "2026-04"
    success: Mapped[bool] = mapped_column(default=True)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
