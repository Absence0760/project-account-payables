"""Saved cash-flow plans — the persisted half of the AI Cash-Flow Copilot.

A :class:`CashPlan` is a **frozen snapshot** of one ``propose_payment_plan``
proposal: the resolved parameters it was computed under, the period-by-period
cash curve it projected, and which discount offers it selected. Saving one is
what makes *plan-vs-actual* possible — comparing what the plan said would leave
the bank in each period against what actually did.

Why a snapshot and not a re-derivation
--------------------------------------
Phases 1–3 deliberately kept plans **stateless**: ``compute_plan_id`` hashes a
plan's own resolved inputs plus the calendar date, and every enact endpoint
re-derives its commitment rows from scratch rather than reading a stored row
(``docs/cash-flow-copilot.md`` §6). That premise still holds for *acting on* a
plan and is untouched here — ``plan_id`` remains the idempotency / replay key,
this table is keyed BY it, and nothing in the enact path reads this table.

What persisting adds is the one thing a re-derivation can never give back:
**what the projection said at the time**. Yesterday's plan is not recomputable
today — its horizon started from a different day and the invoices inside it have
moved on — so without a stored row "did our forecast hold?" is unanswerable. The
snapshot is therefore append-only in spirit: re-saving the same ``plan_id``
returns the existing row untouched rather than restating it against newer data
(which would quietly rewrite the very baseline the comparison rests on).

``entity_id`` semantics
-----------------------
NULL means **consolidated** — a whole-group treasury plan spanning every entity
— NOT the "unstamped legacy row" NULL that :class:`EntityMixin`'s backfill note
describes. This table is new, so no row can be unstamped, and the meaning lines
up with the rest of the stack: ``compute_plan_id`` hashes ``entity_id=None`` for
exactly that scope, ``tenant.apply_entity_scope`` treats ``None`` as the
consolidated view, and ``services/cash_flow_alerts`` already builds its
commitment rows org-wide for the same reason (a shortfall is a question about
the group's cash, not one subsidiary's slice). It is the same *deliberate*
non-NULL-backfilled NULL ``GLAccount`` carries, for a different meaning.

See ``docs/cash-flow-copilot.md`` §5 (Persistence) and §12.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntityMixin, TimestampMixin


class CashPlan(Base, EntityMixin, TimestampMixin):
    __tablename__ = "cash_plans"

    # One saved snapshot per deterministic plan id, per tenant. The id already
    # encodes org + entity + every defining parameter + the calendar date, so
    # this is what makes `POST .../save` idempotent: a retry (a double-clicked
    # button, a replayed request) hits the constraint and returns the existing
    # row rather than storing a second, newer-data snapshot under the same id.
    # Declared here as well as in the migration so a freshly-provisioned tenant
    # (`tenant_provisioning._create_tenant_tables` → `create_all`) gets it too,
    # not only a migrated one.
    __table_args__ = (
        Index("uq_cash_plans_org_plan_id", "organization_id", "plan_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    # `services/cash_flow_plan.compute_plan_id` — a UUID5 string. Same width as
    # `payment_runs.plan_id`, which points at the draft run staged from this
    # same plan.
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # The calendar date the plan was computed for — one of `compute_plan_id`'s
    # own inputs, and the anchor of the plan's horizon window
    # [plan_date, plan_date + horizon_days].
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Optional human label ("Q4 close", "post-audit"). Never used for lookup.
    label: Mapped[str | None] = mapped_column(String(200))

    # ---- resolved defining parameters (the `compute_plan_id` preimage) ----
    granularity: Mapped[str] = mapped_column(String(10), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    min_balance_threshold: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    cash_budget: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    cost_of_capital_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    # ---- the projection itself ----
    # The org's reporting currency at plan time. Every money figure on this row
    # — and every actual it is later compared against — is denominated in it.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    first_shortfall_period: Mapped[str | None] = mapped_column(String(20))
    total_savings_selected: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, default=Decimal("0")
    )
    total_outlay_selected: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, default=Decimal("0")
    )
    # Commitments the curve carried at FACE VALUE in a currency we could not
    # convert. Frozen with the plan because it qualifies every figure above it
    # (see `services/cash_flow_plan.assemble_plan`); a variance computed against
    # a curve with a non-zero count is a figure to resolve, not to act on.
    unconverted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The frozen curve. One object per period, money as EXACT decimal STRINGS
    # (never a JSON number — `float` round-tripping is the money invariant this
    # whole stack is built to avoid). Shape is owned by
    # `services/cash_flow_plan.freeze_periods` / `thaw_periods`:
    #   {period, period_start, period_end, opening, outflow, closing,
    #    below_threshold, unconverted_count}
    periods: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # DiscountOffer ids the plan's optimizer pass selected, and the subset it
    # could not re-time onto the curve. Ids only — no vendor names, no amounts:
    # this row is PII-free by construction.
    selected_offer_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    unretimed_offer_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Control-plane User id of whoever saved it (no cross-DB FK — plain uuid,
    # same as `report_definitions.created_by_user_id`).
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # created_at / updated_at from TimestampMixin.
