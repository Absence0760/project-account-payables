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
class BudgetSpend:
    """Computed spend rollup for one budget. All money is exact ``Decimal``."""

    allocated: Decimal
    committed: Decimal
    actual: Decimal
    remaining: Decimal
    utilization_pct: Decimal
    currency: str


def _q(value) -> Decimal:
    """Coerce a possibly-``None`` SUM result to a 2dp ``Decimal``."""
    return Decimal(value if value is not None else 0)


async def _committed_requisition_total(db: AsyncSession, budget: Budget) -> Decimal:
    """Leg 1 — open, un-converted requisitions linked to this budget."""
    total = (
        await db.execute(
            select(func.coalesce(func.sum(PurchaseRequisition.total), 0)).where(
                PurchaseRequisition.budget_id == budget.id,
                PurchaseRequisition.status.in_(OPEN_COMMITMENT_REQ_STATUSES),
            )
        )
    ).scalar_one()
    return _q(total)


async def _committed_po_total(db: AsyncSession, budget: Budget) -> Decimal:
    """Leg 2 — POs that this budget's converted requisitions turned into."""
    total = (
        await db.execute(
            select(func.coalesce(func.sum(PurchaseOrder.total), 0))
            .select_from(PurchaseRequisition)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseRequisition.converted_po_id)
            .where(
                PurchaseRequisition.budget_id == budget.id,
                PurchaseRequisition.status == RequisitionStatus.converted,
                PurchaseOrder.status.notin_(_DEAD_PO_STATUSES),
            )
        )
    ).scalar_one()
    return _q(total)


async def _actual_invoice_total(db: AsyncSession, budget: Budget) -> Decimal:
    """Realised invoice spend matched to this budget's dimension.

    Every budget dimension maps to a matching ``Invoice`` column (see module
    docstring), so all four — ``cost_center`` / ``gl_account`` / ``department``
    / ``project`` — contribute actual spend."""
    match_col = _DIMENSION_MATCH_COLUMN.get(budget.dimension)
    if match_col is None:
        return Decimal(0)

    conditions = [
        match_col == budget.dimension_value,
        Invoice.status.in_(REALISED_INVOICE_STATUSES),
    ]
    # Bound realised spend to the budget's own period so two budgets tracking the
    # same dimension in different periods don't both report all-time spend. Only
    # applied when both bounds are set; a period-less budget stays all-time.
    if budget.period_start is not None and budget.period_end is not None:
        conditions.append(Invoice.invoice_date.between(budget.period_start, budget.period_end))

    total = (
        await db.execute(select(func.coalesce(func.sum(Invoice.amount), 0)).where(*conditions))
    ).scalar_one()
    return _q(total)


async def compute_budget_spend(db: AsyncSession, budget: Budget) -> BudgetSpend:
    """Compute the full allocated/committed/actual/remaining rollup for a budget.

    All three aggregates SUM in Postgres over ``Numeric`` columns; the arithmetic
    here is ``Decimal`` only. Never float."""
    allocated = _q(budget.amount)
    committed = await _committed_requisition_total(db, budget) + await _committed_po_total(
        db, budget
    )
    actual = await _actual_invoice_total(db, budget)
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
    )
