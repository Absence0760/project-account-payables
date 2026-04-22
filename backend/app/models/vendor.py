import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Vendor(Base, TimestampMixin):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(String(500))
    tax_id: Mapped[str | None] = mapped_column(String(50))
    payment_terms: Mapped[str | None] = mapped_column(String(100))
    bank_details: Mapped[dict | None] = mapped_column(JSONB)
    accepts_virtual_cards: Mapped[bool] = mapped_column(default=False)

    # Vendor status and verification
    status: Mapped[str] = mapped_column(
        String(30), default="active"
    )  # active, unverified, inactive, rejected
    source: Mapped[str] = mapped_column(
        String(30), default="manual"
    )  # manual, erp_sync, ai_extracted
    verified_by: Mapped[str | None] = mapped_column(String(255))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ERP sync
    erp_vendor_id: Mapped[str | None] = mapped_column(String(255))  # vendor ID in the external ERP
    erp_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="vendor_rel")  # noqa: F821
    portal_users: Mapped[list["VendorUser"]] = relationship(  # noqa: F821
        back_populates="vendor", cascade="all, delete-orphan"
    )
