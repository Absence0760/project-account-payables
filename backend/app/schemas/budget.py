"""Pydantic request/response schemas for the procurement budgets router.

Money convention (mirrors ``schemas/expense.py`` / ``schemas/contract.py``):
request fields are typed ``Decimal`` for exactness on the way in; response /
rollup fields serialise money as ``float`` (the router does ``float(...)``).
Never ``float`` on a column or in an in-memory total.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.api.pagination import PageMeta
from app.models.procurement import BudgetDimension

# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


class BudgetBase(BaseModel):
    name: str = Field(..., max_length=255)
    dimension: BudgetDimension = BudgetDimension.department
    dimension_value: str = Field(..., max_length=150)
    period: str | None = Field(default=None, max_length=20)
    period_start: date | None = None
    period_end: date | None = None
    amount: Decimal
    currency: str = Field(default="USD", max_length=3)
    notes: str | None = None


class BudgetCreate(BudgetBase):
    pass


class BudgetUpdate(BaseModel):
    """PATCH — every field optional."""

    name: str | None = Field(default=None, max_length=255)
    dimension: BudgetDimension | None = None
    dimension_value: str | None = Field(default=None, max_length=150)
    period: str | None = Field(default=None, max_length=20)
    period_start: date | None = None
    period_end: date | None = None
    amount: Decimal | None = None
    currency: str | None = Field(default=None, max_length=3)
    notes: str | None = None


class BudgetResponse(BaseModel):
    id: str
    name: str
    dimension: str
    dimension_value: str
    period: str | None
    period_start: str | None
    period_end: str | None
    amount: float
    currency: str
    notes: str | None
    created_at: str
    updated_at: str


class BudgetListResponse(PageMeta):
    items: list[BudgetResponse]
    total: int


class BudgetSpendResponse(BaseModel):
    """Computed spend rollup for one budget. Money serialised as ``float`` for
    display — the exact ``Decimal`` stays in the DB / service layer.

    ``committed`` = open requisitions + their converted POs; ``actual`` =
    realised invoice spend matched to the dimension; ``remaining`` =
    ``allocated - committed - actual`` (negative = overspend)."""

    budget_id: str
    name: str
    dimension: str
    dimension_value: str
    currency: str
    allocated: float
    committed: float
    actual: float
    remaining: float
    utilization_pct: float


class BudgetCheckResponse(BaseModel):
    """Result of ``GET /budgets/check`` — would a proposed ``amount`` overspend
    this budget? Called by the requisition flow before submit. ``remaining`` is
    the pre-check headroom; ``remaining_after`` is what would be left if the
    amount were committed."""

    budget_id: str
    amount: float
    allocated: float
    committed: float
    actual: float
    remaining: float
    remaining_after: float
    would_overspend: bool
    currency: str


__all__ = [
    "BudgetDimension",
    "BudgetBase",
    "BudgetCreate",
    "BudgetUpdate",
    "BudgetResponse",
    "BudgetListResponse",
    "BudgetSpendResponse",
    "BudgetCheckResponse",
]
