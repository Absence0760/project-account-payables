"""Self-service tenant signup: pending email verifications."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EmailVerification(Base, TimestampMixin):
    """Pending signup awaiting email verification.

    Created by POST /api/signup/start. Consumed by POST /api/signup/complete
    after the user clicks the link in the verification email. A tenant is
    NOT provisioned until the verification is consumed.
    """

    __tablename__ = "email_verifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # URL-safe random token sent in the verification email link.
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Payload captured at signup-start; re-validated and used at complete-time.
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    admin_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Free-form extras (ip, user-agent, plan) for audit.
    meta: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
