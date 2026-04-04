import uuid

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    settings: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    plan: Mapped[str] = mapped_column(String(50), default="free")

    # Relationships
    users: Mapped[list["User"]] = relationship(back_populates="organization")  # noqa: F821
    vendors: Mapped[list["Vendor"]] = relationship(back_populates="organization")  # noqa: F821
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="organization")  # noqa: F821
