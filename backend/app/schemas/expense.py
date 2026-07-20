"""Pydantic request/response schemas for the expenses + expense-reports routers.

Money convention (mirrors ``schemas/contract.py``): request fields are typed
``Decimal | None`` for exactness on the way in; response/list fields serialise
money as ``float | None`` (the router does ``float(...)``). Never ``float`` on a
column or in-memory total.

Policy / pre-approval / card-transaction schemas are defined here too so the
later workflows (WF2-4) can reuse them — only the expenses + expense-reports
endpoints are wired in WF1.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.api.pagination import PageMeta
from app.models.expense import (
    ExpensePaymentMethod,
    ExpenseReportStatus,
    ExpenseStatus,
    PreapprovalStatus,
    ReconciliationStatus,
)

# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


class ExpenseBase(BaseModel):
    expense_date: date
    merchant: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = None
    # Must be strictly positive — a negative "expense" would net a report under
    # the CFO approval threshold while hiding a genuinely large line (a credit
    # is a credit memo, not an expense). See issue #156.
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="USD", max_length=3)
    gl_account_id: str | None = None
    payment_method: ExpensePaymentMethod = ExpensePaymentMethod.out_of_pocket
    reimbursable: bool = True
    mileage_miles: Decimal | None = None


class ExpenseCreate(ExpenseBase):
    report_id: str | None = None


class ExpenseUpdate(BaseModel):
    """PATCH — every field optional. ``status`` moves through dedicated flows in
    later workflows, not here."""

    expense_date: date | None = None
    merchant: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, max_length=3)
    gl_account_id: str | None = None
    payment_method: ExpensePaymentMethod | None = None
    reimbursable: bool | None = None
    mileage_miles: Decimal | None = None
    report_id: str | None = None


class ExpenseResponse(BaseModel):
    id: str
    report_id: str | None
    expense_date: str
    merchant: str | None
    category: str | None
    description: str | None
    amount: float
    currency: str
    # Rate-locked expression of `amount` in the owning report's currency (issue
    # #157). Exact decimal strings — new money fields never serialise as float;
    # `amount` above stays float only for back-compat. NULL when unattached.
    converted_amount: str | None = None
    converted_currency: str | None = None
    converted_fx_rate: str | None = None
    converted_fx_locked_at: str | None = None
    gl_account_id: str | None
    receipt_file_key: str | None
    receipt_url: str | None
    payment_method: str
    card_transaction_id: str | None
    policy_violations: list | None
    status: str
    reimbursable: bool
    mileage_miles: float | None
    created_at: str
    updated_at: str


class ExpenseListResponse(PageMeta):
    items: list[ExpenseResponse]
    total: int


# ---------------------------------------------------------------------------
# Expense reports
# ---------------------------------------------------------------------------


class ExpenseReportBase(BaseModel):
    report_number: str = Field(..., max_length=50)
    title: str | None = Field(default=None, max_length=255)
    currency: str = Field(default="USD", max_length=3)
    notes: str | None = None


class ExpenseReportCreate(ExpenseReportBase):
    # The submitting employee. Optional — defaults to the authenticated user.
    employee_user_id: str | None = None


class ExpenseReportUpdate(BaseModel):
    """PATCH — every field optional. Status changes belong to later workflows."""

    report_number: str | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=255)
    currency: str | None = Field(default=None, max_length=3)
    notes: str | None = None


class ExpenseReportAttach(BaseModel):
    """Attach (or, with ``detach=True``, remove) expenses on a report. The
    report's ``total_amount`` is recomputed from its attached expenses."""

    expense_ids: list[str] = Field(default_factory=list)
    detach: bool = False


class ExpenseBulkGlCode(BaseModel):
    """Re-code many expenses onto a single GL account in one call. ``None``
    clears the coding (mirrors the PATCH ``gl_account_id`` null-clearing case)."""

    expense_ids: list[str] = Field(default_factory=list)
    gl_account_id: str | None = None


class ExpenseReportSummary(BaseModel):
    """Aggregate rollup of a report's attached expenses, expressed in the
    report's own ``currency`` via each line's rate-locked conversion — never a
    naive cross-currency sum (issue #157).

    ``total`` stays ``float`` for back-compat with the existing client; the
    exact value is in ``total_exact``. ``unconverted_count`` counts lines with
    no usable rate lock: they are EXCLUDED from the totals (they also block
    submission), so a non-zero value means the displayed figure is partial."""

    total: float
    total_exact: str = "0.00"
    currency: str = "USD"
    count: int
    unconverted_count: int = 0
    by_category: list[dict]
    by_status: list[dict]
    by_currency: list[dict] = Field(default_factory=list)


class ExpenseReportResponse(BaseModel):
    id: str
    report_number: str
    title: str | None
    employee_user_id: str
    status: str
    submitted_at: str | None
    approved_at: str | None
    approved_by: str | None
    total_amount: float
    # Exact `total_amount`, in the report's own `currency`.
    total_amount_exact: str = "0.00"
    currency: str
    # `total_amount` re-expressed in the ORG REPORTING currency at the rate
    # locked on submit — the figure the CFO threshold gate compares (issue
    # #157). NULL before submit, or when no rate was available (the gate then
    # fails closed and requires CFO sign-off).
    reporting_amount: str | None = None
    reporting_currency: str | None = None
    reporting_fx_rate: str | None = None
    reporting_fx_locked_at: str | None = None
    notes: str | None
    expenses: list[ExpenseResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ExpenseReportListResponse(PageMeta):
    items: list[ExpenseReportResponse]
    total: int


# ---------------------------------------------------------------------------
# Policies (defined for WF3 reuse — not wired in WF1)
# ---------------------------------------------------------------------------


class ExpensePolicyBase(BaseModel):
    name: str = Field(..., max_length=255)
    active: bool = True
    category: str | None = Field(default=None, max_length=100)
    # The currency every money threshold on this policy is denominated in.
    # None = "the org's reporting currency", resolved at evaluation time.
    threshold_currency: str | None = None
    per_diem_amount: Decimal | None = None
    per_diem_currency: str = Field(default="USD", max_length=3)
    mileage_rate: Decimal | None = None
    category_limit: Decimal | None = None
    requires_preapproval_above: Decimal | None = None
    requires_receipt_above: Decimal | None = None
    rules: dict | None = None

    @field_validator("threshold_currency")
    @classmethod
    def _normalize_threshold_currency(cls, v: str | None) -> str | None:
        """Uppercase + shape-check the ISO 4217 code (3 letters). Blank → None
        ("unset" = fall back to the org's reporting currency)."""
        if v is None:
            return None
        code = v.strip().upper()
        if not code:
            return None
        if not code.isalpha() or len(code) != 3:
            raise ValueError("threshold_currency must be a 3-letter ISO 4217 code")
        return code


class ExpensePolicyCreate(ExpensePolicyBase):
    pass


class ExpensePolicyUpdate(BaseModel):
    """PATCH — every field optional."""

    name: str | None = Field(default=None, max_length=255)
    active: bool | None = None
    category: str | None = Field(default=None, max_length=100)
    threshold_currency: str | None = None
    per_diem_amount: Decimal | None = None
    per_diem_currency: str | None = Field(default=None, max_length=3)
    mileage_rate: Decimal | None = None
    category_limit: Decimal | None = None
    requires_preapproval_above: Decimal | None = None
    requires_receipt_above: Decimal | None = None
    rules: dict | None = None

    @field_validator("threshold_currency")
    @classmethod
    def _normalize_threshold_currency(cls, v: str | None) -> str | None:
        """Uppercase + shape-check the ISO 4217 code (3 letters). Blank → None
        ("unset" = fall back to the org's reporting currency)."""
        if v is None:
            return None
        code = v.strip().upper()
        if not code:
            return None
        if not code.isalpha() or len(code) != 3:
            raise ValueError("threshold_currency must be a 3-letter ISO 4217 code")
        return code


class ExpensePolicyResponse(BaseModel):
    id: str
    name: str
    active: bool
    category: str | None
    threshold_currency: str | None
    per_diem_amount: float | None
    per_diem_currency: str
    mileage_rate: float | None
    category_limit: float | None
    requires_preapproval_above: float | None
    requires_receipt_above: float | None
    rules: dict | None
    created_at: str
    updated_at: str


class ExpensePolicyListResponse(PageMeta):
    items: list[ExpensePolicyResponse]
    total: int


# ---------------------------------------------------------------------------
# Corporate-card transactions (defined for WF4 reuse — not wired in WF1)
# ---------------------------------------------------------------------------


class CorporateCardTransactionBase(BaseModel):
    card_ref: str | None = Field(default=None, max_length=255)
    card_last_four: str | None = Field(default=None, max_length=4)
    virtual_card_id: str | None = None
    txn_date: date
    posted_date: date | None = None
    merchant: str | None = Field(default=None, max_length=255)
    amount: Decimal
    currency: str = Field(default="USD", max_length=3)
    external_txn_id: str | None = Field(default=None, max_length=255)
    import_batch: str | None = Field(default=None, max_length=100)
    raw: dict | None = None


class CorporateCardTransactionCreate(CorporateCardTransactionBase):
    pass


class CorporateCardMatchRequest(BaseModel):
    """Body for ``POST /corporate-card-transactions/{id}/match`` — the expense
    to reconcile the transaction against."""

    expense_id: str


class CorporateCardMatchSuggestion(BaseModel):
    """One ranked expense candidate for a card transaction."""

    expense: "ExpenseResponse"
    score: float


class CorporateCardTransactionResponse(BaseModel):
    id: str
    card_ref: str | None
    card_last_four: str | None
    virtual_card_id: str | None
    txn_date: str
    posted_date: str | None
    merchant: str | None
    amount: float
    currency: str
    external_txn_id: str | None
    matched_expense_id: str | None
    reconciliation_status: str
    import_batch: str | None
    created_at: str
    updated_at: str


class CorporateCardTransactionListResponse(PageMeta):
    items: list[CorporateCardTransactionResponse]
    total: int


# ---------------------------------------------------------------------------
# Pre-approvals (defined for WF3 reuse — not wired in WF1)
# ---------------------------------------------------------------------------


class ExpensePreapprovalBase(BaseModel):
    title: str = Field(..., max_length=255)
    estimated_amount: Decimal
    currency: str = Field(default="USD", max_length=3)
    category: str | None = Field(default=None, max_length=100)
    justification: str | None = None


class ExpensePreapprovalCreate(ExpensePreapprovalBase):
    requester_user_id: str | None = None


class ExpensePreapprovalResponse(BaseModel):
    id: str
    requester_user_id: str
    title: str
    estimated_amount: float
    currency: str
    category: str | None
    justification: str | None
    status: str
    decided_by: str | None
    decided_at: str | None
    expense_report_id: str | None
    created_at: str
    updated_at: str


class ExpensePreapprovalListResponse(PageMeta):
    items: list[ExpensePreapprovalResponse]
    total: int


class ExpensePreapprovalDecision(BaseModel):
    """Optional body for a pre-approval approve/reject — carries a reason."""

    reason: str | None = None


# ---------------------------------------------------------------------------
# Report approval (WF3) — submit / approve / reject bodies + responses
# ---------------------------------------------------------------------------


class ExpenseReportDecision(BaseModel):
    """Optional body for a report approve/reject — carries a reason."""

    reason: str | None = None


class PolicyViolation(BaseModel):
    """One policy violation surfaced when a report submission is blocked."""

    code: str
    message: str
    policy_id: str | None = None
    limit: str | None = None
    actual: str | None = None
    expense_id: str | None = None


# Re-exported enums so callers can import status types from the schema module.
__all__ = [
    "ExpenseStatus",
    "ExpenseReportStatus",
    "ExpensePaymentMethod",
    "ReconciliationStatus",
    "PreapprovalStatus",
    "ExpenseBase",
    "ExpenseCreate",
    "ExpenseUpdate",
    "ExpenseResponse",
    "ExpenseListResponse",
    "ExpenseReportBase",
    "ExpenseReportCreate",
    "ExpenseReportUpdate",
    "ExpenseReportAttach",
    "ExpenseBulkGlCode",
    "ExpenseReportSummary",
    "ExpenseReportResponse",
    "ExpenseReportListResponse",
    "ExpensePolicyBase",
    "ExpensePolicyCreate",
    "ExpensePolicyUpdate",
    "ExpensePolicyResponse",
    "ExpensePolicyListResponse",
    "CorporateCardTransactionBase",
    "CorporateCardTransactionCreate",
    "CorporateCardTransactionResponse",
    "CorporateCardTransactionListResponse",
    "CorporateCardMatchRequest",
    "CorporateCardMatchSuggestion",
    "ExpensePreapprovalBase",
    "ExpensePreapprovalCreate",
    "ExpensePreapprovalResponse",
    "ExpensePreapprovalListResponse",
    "ExpensePreapprovalDecision",
    "ExpenseReportDecision",
    "PolicyViolation",
]
