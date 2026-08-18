"""Scheduled report model — one row per tenant subscription."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ScheduledReport(Base):
    __tablename__ = "scheduled_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Maps to a key in `services.report_export.EXPORTERS`.
    report_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # 'daily' | 'weekly' | 'monthly'.
    cadence: Mapped[str] = mapped_column(String(20), nullable=False)
    # Email addresses — JSONB list of strings. Stored that way so an
    # admin can edit-in-place via PATCH without a join table.
    recipients: Mapped[list] = mapped_column(JSONB, nullable=False)
    # Window the report covers — passed through to the exporter so
    # the CSV pulls e.g. the last 30 days when this fires.
    period_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 'success' | 'partial' | 'failure' | None (never run yet). `partial` =
    # the report reached some recipients but not all; `next_run_at` still
    # advanced (a retry would duplicate for the ones who got it) and the
    # 5-strike auto-disable does not count it. See
    # `services/scheduled_reports.execute_schedule`.
    last_run_status: Mapped[str | None] = mapped_column(String(20))
    # Truncated to 500 chars so a noisy provider error doesn't blow
    # up the column.
    last_run_error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
