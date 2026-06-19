"""Recurring / subscription invoice templates.

Predictable, fixed-cadence spend (rent, SaaS seats, utilities, insurance)
shouldn't need a fresh upload + extraction every period. A
:class:`RecurringInvoiceTemplate` captures the vendor, amount, GL coding,
entity and a simple cadence (monthly / quarterly / annual + day-of-period);
the ``recurring_invoices`` background sweep generates the next ``Invoice``
on schedule, pre-coded and pre-matched, so it lands straight in the queue.

Idempotency lives at the DB layer, not here: each generated invoice carries
``Invoice.recurring_template_id`` + ``Invoice.recurring_period_key`` and a
partial unique index on that pair means a double-fire of the same period can
never double-create (see ``app.models.invoice``). The template only tracks
``next_run_on`` / ``last_period_key`` for scheduling + display.

Money is ``Numeric`` (never float). See
``backend/docs/recurring-invoices.md``.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntityMixin, TimestampMixin

# Cadence — how often the template generates an invoice.
CADENCE_MONTHLY = "monthly"
CADENCE_QUARTERLY = "quarterly"
CADENCE_ANNUAL = "annual"
CADENCES = (CADENCE_MONTHLY, CADENCE_QUARTERLY, CADENCE_ANNUAL)

# Lifecycle states. `active` generates on schedule; `paused` is temporarily
# suspended (skip generation, keep advancing nothing); `ended` is terminal.
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_ENDED = "ended"
STATUSES = (STATUS_ACTIVE, STATUS_PAUSED, STATUS_ENDED)


class RecurringInvoiceTemplate(Base, EntityMixin, TimestampMixin):
    __tablename__ = "recurring_invoice_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    # Human label for the template, e.g. "Acme Towers — monthly rent".
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Vendor the generated invoices are billed from. Nullable so a template can
    # be drafted before the vendor exists, but the sweep needs it set to
    # generate. `vendor_name` is denormalised for display (mirrors Invoice).
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id"), index=True
    )
    vendor_name: Mapped[str | None] = mapped_column(String(255))

    description: Mapped[str | None] = mapped_column(String(500))

    # Fixed amount stamped onto each generated invoice. Required to generate.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # Pre-coding carried onto every generated invoice.
    gl_account: Mapped[str | None] = mapped_column(String(100))
    cost_center: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(100))
    project: Mapped[str | None] = mapped_column(String(100))
    po_number: Mapped[str | None] = mapped_column(String(100))
    payment_terms: Mapped[str | None] = mapped_column(String(50))

    # Cadence: one of CADENCES. `day_of_period` is the day-of-month (1-28) the
    # invoice is dated/generated on — clamped into range by the scheduler.
    cadence: Mapped[str] = mapped_column(String(20), nullable=False, default=CADENCE_MONTHLY)
    day_of_period: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)

    # The next calendar date the sweep should generate an invoice for. Advanced
    # by the scheduler after each successful generation. NULL = nothing pending
    # (e.g. ended, or past end_date).
    next_run_on: Mapped[date | None] = mapped_column(Date, index=True)
    # period_key of the most recently generated invoice (e.g. "2026-06",
    # "2026-Q2", "2026"). Display + a cheap "already ran this period" guard
    # ahead of the DB unique index.
    last_period_key: Mapped[str | None] = mapped_column(String(40))
    last_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_ACTIVE, index=True
    )

    # Per-template override of the variance tolerance (percent) used to flag an
    # arrived invoice from this vendor that deviates from `amount`. NULL falls
    # back to the org / platform default. Percent, not currency.
    variance_tolerance_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    notes: Mapped[str | None] = mapped_column(String(500))
    meta: Mapped[dict | None] = mapped_column(JSONB)
