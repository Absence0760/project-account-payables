"""Per-vendor correction cache — stores reviewer-confirmed values for
vendor-consistent fields (currency, payment terms, tax rate, etc.) and
reuses them on future extractions for the same vendor.

See backend/docs/ai-extraction.md § Learning from corrections for the
design rationale.

Tenant-scoped — lives in each `feoh_<slug>` database alongside vendors and
invoices.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class VendorExtractionPrior(Base, TimestampMixin):
    __tablename__ = "vendor_extraction_priors"
    __table_args__ = (
        UniqueConstraint("vendor_id", "field_name", name="uq_vendor_priors_vendor_field"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    correction_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
