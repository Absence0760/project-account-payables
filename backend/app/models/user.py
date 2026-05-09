import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))

    users: Mapped[list["User"]] = relationship(secondary="user_roles", back_populates="roles")


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id"), primary_key=True
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sso_provider: Mapped[str | None] = mapped_column(String(50))
    sso_provider_id: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    must_change_password: Mapped[bool] = mapped_column(default=False, nullable=False)

    # MFA — TOTP shared secret + enrollment metadata. `mfa_secret` is the
    # base32-encoded TOTP seed; it's populated during enrollment and only
    # treated as "active" once `mfa_enabled` flips true (after the user
    # successfully verifies a code). The pending secret stays around so the
    # user can scan the QR again without restarting enrollment.
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    mfa_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Delegation — when set and delegate_until > now, this user is OOO and
    # their approval assignments are routed to the delegate.
    delegate_to_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    delegate_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="users")  # noqa: F821
    roles: Mapped[list[Role]] = relationship(secondary="user_roles", back_populates="users")
