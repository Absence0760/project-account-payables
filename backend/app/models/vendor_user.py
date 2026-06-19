"""Supplier-portal user — tenant-scoped, belongs to exactly one Vendor.

Kept separate from `User` (the AP-team model in the control plane) so a bug
in `require_roles` or the JWT decoder can't leak cross-surface: a vendor
JWT rejected outside the portal, an employee JWT rejected inside it.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class VendorUser(Base, TimestampMixin):
    __tablename__ = "vendor_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-portal-user notification preferences. Shape: a map of event_type ->
    # {"email": bool}. Empty `{}` means "use defaults" (all channels on) —
    # see services/notification_dispatch.resolve_prefs. Mirrors
    # `User.notification_prefs` but vendor-scoped: the supplier controls whether
    # they get emailed when their invoices are paid / rejected. Vendors have no
    # in-app notification center, so only the `email` channel is consulted.
    notification_prefs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # MFA — TOTP shared secret + enrollment metadata. Mirrors the columns on
    # `User` (control plane) exactly: `mfa_secret` is the base32 TOTP seed,
    # populated during enrollment and only treated as "active" once
    # `mfa_enabled` flips true (after the vendor verifies a code). The pending
    # secret stays around so the user can re-scan without restarting enrollment.
    # MFA stays opt-in per vendor user and is gated by the `AP_MFA_ENABLED`
    # master switch, exactly like employee MFA. See docs/supplier-portal.md.
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vendor: Mapped["Vendor"] = relationship(back_populates="portal_users")  # noqa: F821
