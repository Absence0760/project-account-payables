import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    settings: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    plan: Mapped[str] = mapped_column(String(50), default="free")
    db_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # sha256(hex) digest of the per-tenant SCIM bearer token. The plaintext
    # is shown to the admin exactly once at mint time and never persisted
    # again. Indexed (unique, partial) so the SCIM auth path can resolve
    # the tenant in O(log n) instead of scanning every org.
    scim_bearer_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)

    # Relationships (only users live in control-plane DB alongside orgs)
    users: Mapped[list["User"]] = relationship(back_populates="organization")  # noqa: F821
