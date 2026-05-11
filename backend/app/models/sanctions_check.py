"""Sanctions-screening audit log — append-only.

Every call to a sanctions provider (OFAC, EU consolidated list, ...)
writes a row here. The orchestrator reads the most recent row per
vendor to gate payments; auditors read the full history to trace
every decision back to the screening that justified it.

Append-only by convention — there are no UPDATE or DELETE call sites
in the codebase. The migration creates indexes for the two read
patterns: (vendor_id, checked_at DESC) for the "most recent check"
lookup, and (result) partial for the "review queue" UI that surfaces
matches and review-required rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SanctionsCheck(Base):
    __tablename__ = "sanctions_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # 'initial' on vendor onboarding, 'periodic' on the scheduled
    # re-screen, 'pre_payment' when called from the orchestrator
    # before a high-risk-corridor payment.
    check_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # 'clear' | 'match' | 'review_required'. A 'match' refuses the
    # payment; 'review_required' opens an exception for AP to triage.
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    matched_list: Mapped[str | None] = mapped_column(String(80))
    raw_response: Mapped[dict | None] = mapped_column(JSONB)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
