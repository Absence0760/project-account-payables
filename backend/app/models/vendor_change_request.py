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

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class VendorChangeRequest(Base, TimestampMixin):
    __tablename__ = "vendor_change_requests"

    # The AP approval queue reads only `pending` rows; an approved/rejected one
    # is history. Partial, so the index stays the size of the queue rather than
    # of the table. Migration 0022's; declared here so `create_all` builds it
    # too.
    __table_args__ = (
        Index(
            "ix_vendor_change_requests_pending",
            "status",
            postgresql_where=text("status = 'pending'"),
        ),
    )

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
    # Frozen at staging time: `VendorUser.provisioned_by_user_id` of the portal
    # identity in `requested_by_vendor_user_id` — i.e. the AP actor who handed
    # out that identity's password, if any.
    #
    # Copied here rather than resolved by a join at approval time for one
    # reason: `DELETE /api/vendors/{id}/portal-users/{id}` carries no FK to this
    # table, so an approver who provisioned the identity could otherwise erase
    # the evidence (delete the portal user) between staging and approving and
    # walk the request through alone. A frozen column cannot be un-stamped by a
    # later delete.
    #
    # NULL is the normal case: a supplier who manages their own credential.
    # `approve_change_request` refuses only when this equals the approver.
    requester_provisioned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
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
