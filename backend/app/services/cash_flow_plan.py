"""Pure payment-plan assembler for the AI Cash-Flow Copilot (Phase 2).

``assemble_plan`` combines a cash-position baseline with the discount
optimizer's selection into a single **proposed plan** artifact: a
period-by-period pay schedule, which discount offers to capture, and the
resulting cash-position curve. It reuses the exact same pure functions the
Phase 1 tools already call (``services.analytics.bucket_outflows`` /
``compute_cash_position``, ``services.discount_optimizer.optimize`` via its
caller) — no DB, no network, no clock; every input (including ``today``) is
passed in, so a call is byte-reproducible.

**This never mutates anything.** It returns a proposal object; enacting it
(draft payment run / discount accept) is Phase 3, unbuilt. See
``docs/cash-flow-copilot.md`` §5.

Re-timing precision
--------------------
A selected recommendation whose opportunity carries an ``invoice_id`` that
matches one of ``rows`` is re-timed onto its ``pay_by`` period at its
discounted outlay (``base_amount - savings``) instead of sitting at the full
amount on its original due date — the plan literally proposes paying that
invoice early. A selected recommendation with no matching row (a
vendor-scoped offer with no single invoice, or an invoice whose due date
falls outside the forecast horizon) still counts toward
``total_savings_selected`` — the optimizer says it is worth capturing — but
there is no row to move, so it is left on its original schedule; its
``offer_id`` is listed in ``unretimed_offer_ids`` so the caller can be
transparent about the gap instead of silently overclaiming precision.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.analytics import bucket_outflows, compute_cash_position
from app.services.discount_optimizer import OptimizationResult


@dataclass(frozen=True)
class PlanPeriod:
    period: str
    period_start: date
    period_end: date
    opening: Decimal
    outflow: Decimal
    closing: Decimal
    below_threshold: bool


@dataclass(frozen=True)
class PlanArtifact:
    granularity: str
    horizon_days: int
    opening_balance: Decimal
    min_balance_threshold: Decimal | None
    periods: list[PlanPeriod]
    first_shortfall_period: str | None
    total_savings_selected: Decimal
    total_outlay_selected: Decimal
    selected_offer_ids: list[str]
    unretimed_offer_ids: list[str]


def assemble_plan(
    rows: list[dict],
    *,
    optimizer_result: OptimizationResult,
    opening_balance: Decimal,
    min_balance_threshold: Decimal | None,
    granularity: str,
    horizon_days: int,
    today: date,
) -> PlanArtifact:
    """Assemble a plan artifact from commitment ``rows`` (the same shape
    ``_commitment_rows`` returns — each carrying an ``invoice_id``) and an
    already-computed ``optimizer_result`` (the same one
    ``/api/discounts/optimize`` and the ``optimize_discount_capture`` tool
    produce for equivalent inputs — this function never re-derives or
    duplicates that selection, only re-times the rows it already chose)."""
    selected = [r for r in optimizer_result.recommendations if r.selected]

    matched_invoice_ids: set[str] = set()
    retimed_rows: list[dict] = []
    unretimed_offer_ids: list[str] = []
    for r in selected:
        inv_id = r.opportunity.invoice_id
        # An invoice can carry more than one open, invoice-scoped offer — the
        # optimizer selects each independently (no dedupe by invoice_id), so a
        # SECOND selected offer on an already-retimed invoice must NOT also be
        # retimed: there is only one row for that invoice, and re-timing it
        # twice would double-count its outflow on the curve. It still counts
        # toward the totals (unchanged, taken from `optimizer_result` as a
        # whole) — just not reflected twice in `periods`.
        matched = (
            inv_id is not None
            and inv_id not in matched_invoice_ids
            and any(row.get("invoice_id") == inv_id for row in rows)
        )
        if matched:
            matched_invoice_ids.add(inv_id)
            outlay = r.opportunity.base_amount - r.roi.savings
            retimed_rows.append(
                {
                    "due_date": r.opportunity.pay_by,
                    "amount": outlay,
                    "committed": True,
                    "discount_date": None,
                    "discount_percent": None,
                }
            )
        else:
            unretimed_offer_ids.append(r.opportunity.offer_id)

    plan_rows = [row for row in rows if row.get("invoice_id") not in matched_invoice_ids]
    plan_rows.extend(retimed_rows)

    plan_periods = bucket_outflows(plan_rows, granularity=granularity, today=today)
    position = compute_cash_position(
        opening_balance, plan_periods, min_balance_threshold=min_balance_threshold
    )

    periods: list[PlanPeriod] = []
    first_shortfall: str | None = None
    for p in position:
        below = bool(p["below_threshold"])
        if below and first_shortfall is None:
            first_shortfall = p["period"]
        periods.append(
            PlanPeriod(
                period=p["period"],
                period_start=p["period_start"],
                period_end=p["period_end"],
                opening=Decimal(str(p["opening"])),
                outflow=Decimal(str(p["outflow"])),
                closing=Decimal(str(p["closing"])),
                below_threshold=below,
            )
        )

    return PlanArtifact(
        granularity=granularity,
        horizon_days=horizon_days,
        opening_balance=Decimal(str(opening_balance or "0")),
        min_balance_threshold=min_balance_threshold,
        periods=periods,
        first_shortfall_period=first_shortfall,
        total_savings_selected=optimizer_result.total_savings_selected,
        total_outlay_selected=optimizer_result.total_outlay_selected,
        selected_offer_ids=[r.opportunity.offer_id for r in selected],
        unretimed_offer_ids=unretimed_offer_ids,
    )


def compute_plan_id(
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    granularity: str,
    horizon_days: int,
    min_balance_threshold: Decimal | None,
    cash_budget: Decimal | None,
    cost_of_capital_pct: Decimal,
    today: date,
) -> str:
    """Deterministic correlation key for a proposed cash-flow plan (Phase 3).

    Per §5/§12 there is no persisted ``CashPlan`` row to look up by primary
    key — a plan is stateless and re-derivable from its own inputs. This
    hashes exactly those inputs (the plan's RESOLVED defining parameters,
    never a raw possibly-``None`` request field — resolution must happen
    before hashing so a ``None`` override on both the propose call and a
    later enact call, which independently resolve to the same org default,
    still hash identically) plus the calendar ``date`` (not a timestamp —
    "today" determines which commitments are in-horizon, so a plan computed
    yesterday and one computed today for identical parameters are, correctly,
    two different plans).

    ``propose_payment_plan`` computes this once and returns it on the tool
    result as ``plan_id``; the enact endpoints
    (``POST /api/cash-flow/plans/{plan_id}/{draft-run,capture-discounts}``)
    recompute it from the SAME resolution over the client's replayed
    parameters and refuse (409) on a mismatch rather than trusting the
    caller about which plan to act on. See docs/cash-flow-copilot.md §5/§6.

    UUID5 (not ``uuid4``) so the id is a pure function of its inputs, not
    random state — the whole point of the scheme.
    """
    parts = "|".join(
        [
            str(org_id),
            str(entity_id) if entity_id is not None else "-",
            granularity,
            str(horizon_days),
            str(min_balance_threshold) if min_balance_threshold is not None else "-",
            str(cash_budget) if cash_budget is not None else "-",
            str(cost_of_capital_pct),
            today.isoformat(),
        ]
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"feoh:cashflow-plan:{parts}"))
