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

# ---------------------------------------------------------------------------
# Which template edits implicate their editor in segregation of duties.
#
# A template is a standing instruction to create payables, so whoever shapes the
# instruction has shaped every payable it raises — and must not also approve one.
# ``created_by_user_id`` records the author; ``material_editor_ids`` records
# everyone who later changed a term of the payable. ``api/recurring``'s
# ``update_template`` appends the actor when, and only when, a field in
# MATERIAL_EDIT_FIELDS actually changes value.
#
# "Material" means *a term of the generated payable*: who is paid, how much, in
# what currency, against which budget line or PO, and on what schedule.
# Everything else is a label on the template.
#
# Deliberately excluded, and why:
#   * ``name`` / ``description`` / ``notes`` — prose. Renaming "Acme rent" does
#     not change a cent of what gets raised, and implicating a typo-fixer spends
#     the control's credibility for nothing.
#   * ``variance_tolerance_pct`` — a detection band for *arrived* vendor invoices
#     that deviate from ``amount``. It never touches the invoice this template
#     generates, and an arrived invoice carries its own uploader.
#   * the pause / resume / end lifecycle endpoints — they change whether the
#     instruction is live, not what it instructs. Resuming someone else's
#     template does not choose its vendor or its amount, so the second pair of
#     eyes the control wants is still a real one.
#
# ``entity_id`` is listed even though ``RecurringTemplateUpdate`` cannot
# currently reach it: it decides which legal entity owes the payable, which is an
# approval scope, so the day a PATCH can move it the set already covers it.
#
# The two sets must between them cover every PATCHable field. A new money- or
# schedule-shaped field that nobody classified would default to "cosmetic" and
# silently widen the exemption, so ``tests/test_recurring_invoices.py`` fails
# until a new ``RecurringTemplateUpdate`` field lands in exactly one of them.
MATERIAL_EDIT_FIELDS: frozenset[str] = frozenset(
    {
        # Who gets paid. (``vendor_name`` is denormalised from ``vendor_id`` by
        # the router and never independently edited, so only the id is listed.)
        "vendor_id",
        # How much, and in what.
        "amount",
        "currency",
        # Budget attribution + the 3-way-match target.
        "gl_account",
        "cost_center",
        "department",
        "project",
        "po_number",
        # When money leaves, and therefore the early-pay discount window.
        "payment_terms",
        # How often a payable is raised, and from / until when.
        "cadence",
        "day_of_period",
        "start_date",
        "end_date",
        # Which legal entity owes it.
        "entity_id",
    }
)

COSMETIC_EDIT_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "notes",
        "variance_tolerance_pct",
    }
)


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

    # The control-plane ``User`` who created this template — the employee whose
    # standing instruction every generated invoice is. No ForeignKey: ``users``
    # lives in the control plane and this table is tenant-local, the same
    # placement as ``Invoice.uploaded_by_id`` and
    # ``VendorUser.provisioned_by_user_id``.
    #
    # It exists for segregation of duties. The background sweep has no human
    # actor, so ``generate_one`` used to stamp the generated invoice's
    # ``uploaded_by_id`` NULL — and ``approval_chain.violates_segregation``
    # reads NULL as "no employee created this row" and returns False. The
    # template's author could therefore approve the invoice their own template
    # raised. Unlike email intake or inbound PEPPOL there IS an employee here;
    # we simply had nowhere to record them. ``generate_one`` now falls back to
    # this column when there is no live actor.
    #
    # Nullable and never backfilled: a template that predates the column has no
    # honest author to recover, and inventing one would manufacture either a
    # refusal or an absolution. Such rows keep the legacy NULL reading.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Every control-plane ``User`` who has made a MATERIAL edit to this template
    # (see ``MATERIAL_EDIT_FIELDS`` above) — a set, stored as a sorted JSONB
    # array of stringified UUIDs. Same no-ForeignKey placement as
    # ``created_by_user_id``, for the same reason: ``users`` is control-plane.
    #
    # ``created_by_user_id`` closed the sweep's exemption for the *author* and
    # left it open for the editor: an ap_manager who repoints someone else's
    # template at a different vendor and amount has shaped the payable
    # completely, was recorded nowhere on it, and could still approve what it
    # generated. Stamping the editor into ``created_by_user_id`` instead would
    # only have moved the exemption to the author — no single column holds both,
    # which is why segregation now keys on a set
    # (``approval_chain.violates_segregation``) and ``generate_one`` stamps
    # author ∪ editors onto ``Invoice.segregation_actor_ids``.
    #
    # Nullable and never backfilled, for decisions §141's reason: there is no
    # honest editor to recover for an edit made before the column existed, and
    # every available proxy (the last updater, the org admin) manufactures
    # either a refusal or an absolution. ``updated_at`` records *that* someone
    # edited, never who.
    material_editor_ids: Mapped[list | None] = mapped_column(JSONB)
