"""Budget spend-rollup logic — computed on read (no stored running total).

A ``Budget`` row carries only the *allocation* (``amount``). Everything that
has been spent or committed against it is derived live from the procurement +
AP tables, so the numbers never drift out of sync with the underlying activity.

Money is exact end-to-end: every aggregate is a Postgres ``SUM`` over a
``Numeric(15, 2)`` column coerced to ``Decimal`` (never float). The API layer
serialises the resulting ``Decimal`` to ``float`` for display only — mirroring
the expense-reporting rollup (``api/expenses.py::report_summary``).

Spend definitions (the contract the ``/spend`` + ``/check`` endpoints expose):

  allocated  — ``budget.amount``. The cap for this dimension/period.

  committed  — money that is earmarked but not yet invoiced. Two legs, summed:
                 1. ``PurchaseRequisition.total`` for requisitions linked to the
                    budget (``budget_id == budget.id``) whose status is one of
                    the OPEN-COMMITMENT statuses — ``submitted``,
                    ``pending_approval``, ``approved`` — i.e. live demand that
                    has NOT yet become a PO.
                 2. ``PurchaseOrder.total`` for the POs those budget-linked
                    requisitions converted into (``status == 'converted'`` reqs,
                    joined to ``purchase_orders`` via ``converted_po_id``),
                    excluding cancelled/closed POs.
               A converted requisition's amount is represented by its PO (leg 2),
               NOT by the requisition (leg 1) — the OPEN-COMMITMENT status list
               deliberately omits ``converted`` so the two legs never
               double-count the same demand.

  actual     — money already invoiced against this budget's dimension. Invoices
               do not carry a ``budget_id``, so they are attributed by matching
               the budget's ``dimension`` to the corresponding ``Invoice``
               column — one column per dimension, all four covered:
                 - ``cost_center``  → ``Invoice.cost_center == dimension_value``
                 - ``gl_account``   → ``Invoice.gl_account  == dimension_value``
                 - ``department``   → ``Invoice.department  == dimension_value``
                 - ``project``      → ``Invoice.project     == dimension_value``
               Only invoices in a REALISED-SPEND status count — ``approved``,
               ``payment_scheduled``, ``paid``, ``posted_in_erp``, ``sent_to_erp``,
               ``done`` — so a brand-new / rejected invoice never inflates actual.
               When the budget carries both ``period_start`` and ``period_end``,
               actual is further bounded to invoices whose ``invoice_date`` falls
               inside that window, so two budgets tracking the same dimension in
               different periods don't both report all-time spend.

  remaining  — ``allocated - committed - actual`` (can go negative = overspend).

  utilization — ``(committed + actual) / allocated`` as a percentage, rounded to
               2 dp. ``0`` when allocated is 0 (avoid div-by-zero).

The **actual** (invoice) leg is scoped to the budget's own entity
(``apply_entity_scope`` on ``budget.entity_id``) so a subsidiary's budget never
picks up a sibling subsidiary's spend on a shared free-text dimension value.
The two **committed** legs are NOT: they key off
``PurchaseRequisition.budget_id``, an unambiguous human-declared link, so an
entity filter there could only drop deliberately-linked demand — see the note
above ``_committed_requisition_total``.

Every leg is scoped to the budget's own ``currency`` — the legs never convert,
so mixing currencies would add unlike face values (a EUR and a USD invoice on
the same cost center are NOT summed).
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.procurement import (
    Budget,
    BudgetDimension,
    PurchaseOrder,
    PurchaseRequisition,
    RequisitionStatus,
)
from app.tenant import apply_entity_scope

# Requisition statuses that represent a live, un-converted commitment against a
# budget. ``converted`` is intentionally excluded — its spend is counted via the
# resulting PO instead (see module docstring).
OPEN_COMMITMENT_REQ_STATUSES: tuple[RequisitionStatus, ...] = (
    RequisitionStatus.submitted,
    RequisitionStatus.pending_approval,
    RequisitionStatus.approved,
)

# PO statuses that no longer represent a live commitment (excluded from the PO
# leg of committed). Everything else (e.g. ``open``, ``received``) counts.
_DEAD_PO_STATUSES: tuple[str, ...] = ("cancelled", "closed", "voided")

# Maps each budget dimension to the ``Invoice`` column that carries that
# dimension's value. Realised invoice spend is attributed by ``column ==
# budget.dimension_value`` (invoices carry no ``budget_id``).
_DIMENSION_MATCH_COLUMN = {
    BudgetDimension.cost_center: Invoice.cost_center,
    BudgetDimension.gl_account: Invoice.gl_account,
    BudgetDimension.department: Invoice.department,
    BudgetDimension.project: Invoice.project,
}

# Invoice statuses that represent realised spend against a budget dimension.
REALISED_INVOICE_STATUSES: tuple[str, ...] = (
    "approved",
    "sent_to_erp",
    "posted_in_erp",
    "payment_scheduled",
    "paid",
    "done",
)


@dataclass(frozen=True)
class _Leg:
    """One spend leg's exact total plus the rows it REFUSED.

    ``excluded`` is a COUNT, not money: rows linked to (or matching) this budget
    whose currency is not the budget's — or is NULL. They are deliberately not
    summed (the legs never convert) and deliberately not forgotten."""

    total: Decimal
    excluded: int


@dataclass(frozen=True)
class BudgetSpend:
    """Computed spend rollup for one budget. All money is exact ``Decimal``."""

    allocated: Decimal
    committed: Decimal
    actual: Decimal
    remaining: Decimal
    utilization_pct: Decimal
    currency: str
    # How many rows the three legs REFUSED for being denominated in another
    # currency than the budget (or carrying no currency at all). The legs never
    # convert, so excluding is the right call — but a figure that quietly left
    # rows out reads exactly like a complete one, which is the defect the
    # `unconverted_count` disclosures elsewhere exist to prevent. Non-zero means
    # `committed` / `actual` describe PART of the linked activity; the reader
    # has to be told at the point of reading (`docs/decisions.md` §35).
    excluded_row_count: int = 0


def _q(value) -> Decimal:
    """Coerce a possibly-``None`` SUM result to a 2dp ``Decimal``.

    The quantize is load-bearing, not cosmetic: a ``coalesce(SUM(...), 0)`` over
    an empty set comes back as the integer ``0``, so an un-quantized leg
    serialised as ``"0"`` next to a sibling's ``"260.05"`` in the same rollup.
    Every money column behind these SUMs is ``Numeric(15, 2)``, so 2dp is the
    scale the data already has — this only pins the empty case to it."""
    return Decimal(value if value is not None else 0).quantize(Decimal("0.01"))


# Why the two `budget_id`-keyed legs below are NOT entity-scoped, unlike the
# invoice leg:
#
# `PurchaseRequisition.budget_id == budget.id` is an UNAMBIGUOUS link — a human
# said "this demand spends this budget". Layering `apply_entity_scope(...,
# budget.entity_id)` on top of it can only ever REMOVE deliberately-linked
# demand, so `committed` read 0 and `/budgets/check` answered
# `would_overspend: false` for headroom already spoken for. The invoice leg is
# different and keeps its scoping: attribution there is a fuzzy free-text
# `dimension_value` match, where narrowing is genuinely protective.
#
# The CURRENCY predicate stays. `POST`/`PATCH /api/requisitions` now refuses a
# link whose currency doesn't match the budget's (422), so it can only bite a
# row linked before that guard existed — and summing two currencies' face
# values into one total would be worse than excluding the row ("money is
# exact"; the legs never convert).


async def _committed_requisition_total(db: AsyncSession, budget: Budget) -> _Leg:
    """Leg 1 — open, un-converted requisitions linked to this budget."""
    matches = PurchaseRequisition.currency == budget.currency
    query = select(
        func.coalesce(func.sum(PurchaseRequisition.total).filter(matches), 0),
        func.count().filter(matches.isnot(True)),
    ).where(
        PurchaseRequisition.budget_id == budget.id,
        PurchaseRequisition.status.in_(OPEN_COMMITMENT_REQ_STATUSES),
    )
    total, excluded = (await db.execute(query)).one()
    return _Leg(_q(total), int(excluded or 0))


async def _committed_po_total(db: AsyncSession, budget: Budget) -> _Leg:
    """Leg 2 — POs that this budget's converted requisitions turned into."""
    # PurchaseOrder carries no currency; the requisition it converted from does,
    # and the two share it.
    matches = PurchaseRequisition.currency == budget.currency
    query = (
        select(
            func.coalesce(func.sum(PurchaseOrder.total).filter(matches), 0),
            func.count().filter(matches.isnot(True)),
        )
        .select_from(PurchaseRequisition)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseRequisition.converted_po_id)
        .where(
            PurchaseRequisition.budget_id == budget.id,
            PurchaseRequisition.status == RequisitionStatus.converted,
            PurchaseOrder.status.notin_(_DEAD_PO_STATUSES),
        )
    )
    total, excluded = (await db.execute(query)).one()
    return _Leg(_q(total), int(excluded or 0))


async def _actual_invoice_total(db: AsyncSession, budget: Budget) -> _Leg:
    """Realised invoice spend matched to this budget's dimension.

    Every budget dimension maps to a matching ``Invoice`` column (see module
    docstring), so all four — ``cost_center`` / ``gl_account`` / ``department``
    / ``project`` — contribute actual spend."""
    match_col = _DIMENSION_MATCH_COLUMN.get(budget.dimension)
    if match_col is None:
        return _Leg(Decimal(0), 0)

    conditions = [
        match_col == budget.dimension_value,
        Invoice.status.in_(REALISED_INVOICE_STATUSES),
    ]
    # Bound realised spend to the budget's own period so two budgets tracking the
    # same dimension in different periods don't both report all-time spend. Only
    # applied when both bounds are set; a period-less budget stays all-time.
    if budget.period_start is not None and budget.period_end is not None:
        conditions.append(Invoice.invoice_date.between(budget.period_start, budget.period_end))

    # Only sum invoices in the budget's own currency — the legs never convert,
    # so mixing currencies would add unlike face values. The refused rows are
    # COUNTED rather than forgotten (see `BudgetSpend.excluded_row_count`).
    matches = Invoice.currency == budget.currency
    query = select(
        func.coalesce(func.sum(Invoice.amount).filter(matches), 0),
        func.count().filter(matches.isnot(True)),
    ).where(*conditions)
    query = apply_entity_scope(query, Invoice, budget.entity_id)
    total, excluded = (await db.execute(query)).one()
    return _Leg(_q(total), int(excluded or 0))


async def compute_budget_spend(db: AsyncSession, budget: Budget) -> BudgetSpend:
    """Compute the full allocated/committed/actual/remaining rollup for a budget.

    All three aggregates SUM in Postgres over ``Numeric`` columns; the arithmetic
    here is ``Decimal`` only. Never float."""
    allocated = _q(budget.amount)
    req_leg = await _committed_requisition_total(db, budget)
    po_leg = await _committed_po_total(db, budget)
    actual_leg = await _actual_invoice_total(db, budget)
    committed = req_leg.total + po_leg.total
    actual = actual_leg.total
    remaining = allocated - committed - actual

    if allocated > 0:
        utilization = ((committed + actual) / allocated * Decimal(100)).quantize(Decimal("0.01"))
    else:
        utilization = Decimal("0.00")

    return BudgetSpend(
        allocated=allocated,
        committed=committed,
        actual=actual,
        remaining=remaining,
        utilization_pct=utilization,
        currency=budget.currency,
        excluded_row_count=req_leg.excluded + po_leg.excluded + actual_leg.excluded,
    )


# ---------------------------------------------------------------------------
# Org-wide rollup — allocated vs committed vs actual across many budgets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetCurrencyRollup:
    """One currency's slice of the org-wide budget-vs-actual rollup.

    Every figure is the exact sum of the per-budget ``BudgetSpend`` values for
    budgets denominated in ``currency``. Nothing is ever added ACROSS
    currencies and nothing is FX-converted here — an exchange rate fetched on a
    read makes the answer non-deterministic (``backend/docs/multi-currency.md``),
    and a blended figure is not denominated in anything real.
    """

    currency: str
    budget_count: int
    allocated: Decimal
    committed: Decimal
    actual: Decimal
    remaining: Decimal
    # ``None``, never ``0``, when this currency's budgets allocate nothing at
    # all: "0% of the budget is used" and "there is no budget to use" are
    # opposite facts, and 0% renders as the reassuring one. Same rule
    # ``analytics.compute_discount_capture`` applies to ``capture_rate_pct``
    # (``docs/decisions.md`` §34).
    utilization_pct: Decimal | None
    over_budget_count: int
    excluded_row_count: int


@dataclass(frozen=True)
class BudgetRollup:
    """Org-wide budget-vs-actual rollup over a set of budgets.

    ``insufficient_data`` is true when the filtered set holds no budgets at all
    — the caller renders "no budgets" rather than a row of confident zeros.
    ``excluded_row_count`` is the whole-set total of the per-currency
    disclosures: non-zero means the committed/actual figures below describe
    PART of the linked activity, and the surface rendering them must say so.
    """

    budget_count: int
    by_currency: list[BudgetCurrencyRollup]
    excluded_row_count: int
    insufficient_data: bool


def _resolve_currency(code: str | None) -> str:
    """Uppercase a budget's currency code; blank/NULL falls back to ``USD``.

    Matches how ``api/budgets.py::budget_summary`` keys its group-by, so the
    rollup and the allocation KPI bucket the same rows the same way."""
    return (code or "").strip().upper() or "USD"


async def compute_budget_rollup(db: AsyncSession, budgets: "list[Budget]") -> BudgetRollup:
    """Fold ``compute_budget_spend`` over ``budgets``, grouped by currency.

    Compute-on-read like the per-budget rollup it is built from — there is no
    stored running total to drift. Cost is three aggregate queries per budget;
    the set is deliberately the WHOLE filtered set rather than a page, because a
    partial rollup presented as an org-wide total is exactly the dishonesty the
    per-currency grouping exists to prevent.

    Ordering is by currency code ascending — deterministic, and stable as the
    amounts move (a total-ordered list would reshuffle between reads).
    """
    buckets: dict[str, dict] = {}
    for budget in budgets:
        spend = await compute_budget_spend(db, budget)
        code = _resolve_currency(budget.currency)
        b = buckets.setdefault(
            code,
            {
                "budget_count": 0,
                "allocated": Decimal(0),
                "committed": Decimal(0),
                "actual": Decimal(0),
                "over_budget_count": 0,
                "excluded_row_count": 0,
            },
        )
        b["budget_count"] += 1
        b["allocated"] += spend.allocated
        b["committed"] += spend.committed
        b["actual"] += spend.actual
        b["excluded_row_count"] += spend.excluded_row_count
        if spend.remaining < 0:
            b["over_budget_count"] += 1

    by_currency = []
    for code in sorted(buckets):
        b = buckets[code]
        allocated: Decimal = b["allocated"]
        committed: Decimal = b["committed"]
        actual: Decimal = b["actual"]
        utilization = (
            ((committed + actual) / allocated * Decimal(100)).quantize(Decimal("0.01"))
            if allocated > 0
            else None
        )
        by_currency.append(
            BudgetCurrencyRollup(
                currency=code,
                budget_count=b["budget_count"],
                allocated=allocated,
                committed=committed,
                actual=actual,
                remaining=allocated - committed - actual,
                utilization_pct=utilization,
                over_budget_count=b["over_budget_count"],
                excluded_row_count=b["excluded_row_count"],
            )
        )

    return BudgetRollup(
        budget_count=len(budgets),
        by_currency=by_currency,
        excluded_row_count=sum(c.excluded_row_count for c in by_currency),
        insufficient_data=len(budgets) == 0,
    )
