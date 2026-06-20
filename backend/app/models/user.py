import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    # Role-name uniqueness, the invariant `provision_tenant` and the RBAC
    # checks rely on:
    #   * uq_roles_name_org  — plain UNIQUE(name, organization_id) from
    #     migration 0014. Keeps org-scoped custom roles unique within an org,
    #     but does NOT constrain system roles: Postgres treats two NULL
    #     organization_ids as distinct, so duplicate `admin` rows could slip in
    #     (and did — breaking provision_tenant's `scalar_one_or_none()` role
    #     lookup with MultipleResultsFound). Declared here too so create_all
    #     (CI / fresh control DBs) matches what migrations produce.
    #   * uq_roles_system_name — partial unique on `name` WHERE org_id IS NULL,
    #     which closes that gap. Added in migration 0028 for existing DBs.
    __table_args__ = (
        UniqueConstraint("name", "organization_id", name="uq_roles_name_org"),
        Index(
            "uq_roles_system_name",
            "name",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    # NULL = system role (admin / ap_manager / ap_clerk / cfo) — applies
    # across every tenant. Non-NULL = a custom role minted by an org admin
    # via /api/admin/roles. Uniqueness is on (name, organization_id), so
    # two orgs can both define their own "Approver" without collision.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

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

    # Per-user notification preferences. Shape: a map of event_type ->
    # {"email": bool, "in_app": bool}. Empty `{}` means "use defaults"
    # (all channels on) — see services/notification_dispatch.resolve_prefs.
    # User-global (preferences aren't tenant-scoped), so they live here on the
    # control-plane User rather than in any tenant DB.
    notification_prefs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # Account-level email language preference — "what language to email this
    # person in" (signup/welcome, invoice notifications, supplier chat). NULL =
    # English fallback. Validated against the supported set at the write path
    # (PATCH /api/auth/me); read ONLY by the email-rendering path
    # (services/email_adapters/email_catalogue.py), never to drive in-app UI —
    # that's the frontend's separate per-device locale. See docs/notifications.md.
    locale: Mapped[str | None] = mapped_column(String(16))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="users")  # noqa: F821
    roles: Mapped[list[Role]] = relationship(secondary="user_roles", back_populates="users")
