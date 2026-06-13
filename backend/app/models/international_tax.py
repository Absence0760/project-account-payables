"""International-tax record model — one row per taxed invoice/payment.

Tenant-scoped. Persists the computed VAT / GST / withholding figures for an
invoice so the per-period tax report can aggregate "collected vs owed"
without recomputing from scratch (rates can drift; the persisted figure is
the audit fact). Money columns are ``Numeric`` (never ``Float``) — the
*money is exact* invariant.

No PII / banking data is stored here — only the country code, regime, rates,
and Decimal tax amounts.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TaxKind(enum.StrEnum):
    vat = "vat"
    gst = "gst"
    withholding = "withholding"


class IntlTaxRecord(Base):
    __tablename__ = "intl_tax_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Optional link to the invoice / payment the tax was computed for.
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    kind: Mapped[TaxKind] = mapped_column(
        Enum(TaxKind, native_enum=False, length=20), nullable=False
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    region: Mapped[str | None] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    net_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    # For VAT this is what's actually owed in cash to the supplier (0 under
    # reverse charge); for withholding it's the net paid to the supplier.
    # NULL when not applicable to the kind.
    settled_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    # True for reverse-charge VAT rows (reportable but no cash VAT).
    reverse_charge: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Component breakdown for GST (cgst/sgst/igst/pst) — Decimals serialised
    # as strings in JSONB so no float ever enters the trail.
    components: Mapped[dict | None] = mapped_column(JSONB)

    # The accounting period the row falls in (used by the report query).
    tax_point_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
