"""Bank reconciliation models — `bank_statements` + `bank_transactions`.

A `BankStatement` is one uploaded CSV / OFX / camt.053 file covering
a date range on a single account. Its `BankTransaction` children are
the parsed line items; each carries an optional FK to the
`Payment` row we believe it matches, plus the match method and
confidence.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class BankStatement(Base):
    __tablename__ = "bank_statements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Free-form account label (the last-4 of the account number,
    # or the org's chosen alias — e.g. "Operating ****1234").
    account_identifier: Mapped[str] = mapped_column(String(80), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    # One of 'csv' | 'ofx' | 'camt053'. The importer reads this to
    # pick the parser.
    source_format: Mapped[str] = mapped_column(String(20), nullable=False)
    # S3 key for the original file — kept for audit replay.
    file_key: Mapped[str | None] = mapped_column(String(512))
    imported_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    transaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transactions: Mapped[list[BankTransaction]] = relationship(
        back_populates="statement", cascade="all, delete-orphan"
    )


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_statements.id"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    posted_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    counterparty_name: Mapped[str | None] = mapped_column(String(255))
    reference: Mapped[str | None] = mapped_column(String(255))
    # 'debit' (money leaving the account — outgoing payment) or
    # 'credit' (money arriving — usually inbound, not our concern).
    # Reconciliation matches debits against the Payment table.
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    # Match result — populated by `services/bank_reconciliation`.
    matched_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id")
    )
    match_method: Mapped[str | None] = mapped_column(String(40))
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    statement: Mapped[BankStatement] = relationship(back_populates="transactions")
