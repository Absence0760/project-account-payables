"""Chart of Accounts — GL account codes synced from ERP or manually managed."""

import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class GLAccount(Base, TimestampMixin):
    __tablename__ = "gl_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(50))  # expense, asset, liability, revenue
    parent_code: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(default=True)
    erp_account_id: Mapped[str | None] = mapped_column(String(255))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
