"""Supplier-portal change-request staging table — tenant-scoped.

A vendor-portal user can edit non-sensitive contact fields (phone, address,
email) live, but a change to `bank_details` or `tax_id` stages a pending row
here instead of mutating the `Vendor`. AP approves/rejects each row; only on
approval does the staged value get applied to the vendor. This is the
fraud-prevention core of supplier self-service — a redirected bank account
can't take effect without an AP admin's explicit sign-off.

`proposed_value` carries banking PII; it is never written to logs (audit
breadcrumbs log only `{change_type, request_id, last4}`).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class VendorChangeRequest(Base, TimestampMixin):
    __tablename__ = "vendor_change_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # The portal user (VendorUser.id) who requested the change. NULL for an
    # AP-initiated request (then `requested_by_user_id` is set instead).
    requested_by_vendor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # The control-plane User who staged the change from the AP app (NULL for a
    # portal-submitted request). Exactly one of the two requester columns is set;
    # the approve path uses this to enforce requester != approver (SoD).
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # 'bank_details' | 'tax_id'
    change_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # pending | approved | rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # {"bank_details": {...}} or {"tax_id": "..."} — banking PII, never logged.
    proposed_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # The AP user (control-plane User.id) who reviewed it.
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
