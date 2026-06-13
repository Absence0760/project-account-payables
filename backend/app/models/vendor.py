import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, EntityMixin, TimestampMixin


class Vendor(Base, EntityMixin, TimestampMixin):
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

    # 1099 / W-9 tax data. See backend/docs/tax-1099.md.
    # tax_classification: IRS entity type — individual, sole_proprietor,
    # llc_s_corp, llc_c_corp, llc_partnership, c_corp, s_corp, partnership,
    # trust, other. Populated from the W-9 form box 3.
    tax_classification: Mapped[str | None] = mapped_column(String(50))
    # Flipped by AP when a W-9 is collected for a 1099-eligible vendor.
    # Corporations (c_corp/s_corp) are generally NOT 1099-eligible; we
    # leave this to tenant judgement rather than auto-deriving.
    is_1099_eligible: Mapped[bool] = mapped_column(default=False)
    w9_received_date: Mapped[date | None] = mapped_column(Date)
    w9_file_key: Mapped[str | None] = mapped_column(String(512))
    tin_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # KYC / AML. Drives whether `services.compliance` lets a
    # high-risk-corridor payment through. See migration 0018 and
    # `docs/international-payments.md` § KYC/AML.
    kyc_status: Mapped[str] = mapped_column(
        String(20), default="not_required"
    )  # pending | verified | rejected | not_required
    kyc_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kyc_verified_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    beneficial_owner_data: Mapped[dict | None] = mapped_column(JSONB)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="vendor_rel")  # noqa: F821
    portal_users: Mapped[list["VendorUser"]] = relationship(  # noqa: F821
        back_populates="vendor", cascade="all, delete-orphan"
    )
