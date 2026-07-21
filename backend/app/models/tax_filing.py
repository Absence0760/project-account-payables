"""1099 e-filing batch record — the idempotency + audit anchor for filing.

One row per (organization, tax_year, form_type, idempotency_key) submission.
The unique constraint on ``(organization_id, idempotency_key)`` is what makes
``POST /api/tax/1099/file`` idempotent at the data layer: a retried submit
with the same key hits the existing row and returns the stored confirmation
instead of re-filing with the partner. Filing a 1099 is effectively a
money-/compliance-moving write (a duplicate IRS filing is a real problem), so
it needs the same idempotency discipline as the payment path.

The row is inserted (status=``pending``) and flushed to CLAIM the idempotency
slot *before* the partner call — a concurrent duplicate submit hits the
unique constraint immediately instead of both requests reaching the partner.
``pending`` is only ever transient: the endpoint either overwrites it with the
real outcome (``accepted``/``rejected``/``partial``) once the partner
responds, or deletes the row if the partner call itself fails (so a
legitimate retry isn't permanently blocked).

Tenant-scoped table. Carries NO recipient TIN — only counts, the confirmation
number, and the redacted per-form result list (vendor_id + accepted +
reason_code) in ``result``. A TIN never enters this table.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Tax1099Filing(Base, TimestampMixin):
    __tablename__ = "tax_1099_filings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_tax_1099_filing_idempotency"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # Caller-supplied (or derived) key; the unique constraint above keys the
    # idempotent submit on it.
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # accepted|rejected|partial
    confirmation_number: Mapped[str | None] = mapped_column(String(120))
    submitted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Redacted per-form result (vendor_id, form_type, accepted, reason_code).
    # No TIN. Mirrors FilingBatchResult.to_dict()["forms"].
    result: Mapped[dict | None] = mapped_column(JSONB)
