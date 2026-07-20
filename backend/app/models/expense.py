"""Expense management — out-of-pocket + corporate-card expenses, reports,
policies, pre-approvals, and corporate-card-transaction reconciliation.

Five tenant-scoped tables in one module (mirrors contract.py / supplier_chat.py
patterns): inline ``organization_id`` + ``EntityMixin`` on every business table,
StrEnum status types mapped to ``Enum(..., native_enum=False, length=N)`` String
columns, money as ``Numeric(15, 2)`` (never float). A circular FK between
``expenses.card_transaction_id`` and ``corporate_card_transactions.matched_expense_id``
is broken with ``use_alter=True`` on one side so ``metadata.create_all`` orders
the DDL itself. See ``backend/docs/expense-management.md``.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, EntityMixin, TimestampMixin


class ExpenseReportStatus(enum.StrEnum):
    draft = "draft"
    submitted = "submitted"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    reimbursed = "reimbursed"
    cancelled = "cancelled"


class ExpenseStatus(enum.StrEnum):
    draft = "draft"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"
    reimbursed = "reimbursed"


class ExpensePaymentMethod(enum.StrEnum):
    out_of_pocket = "out_of_pocket"
    corporate_card = "corporate_card"
    virtual_card = "virtual_card"


class ReconciliationStatus(enum.StrEnum):
    unmatched = "unmatched"
    matched = "matched"
    ignored = "ignored"


class PreapprovalStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ExpenseReport(Base, EntityMixin, TimestampMixin):
    """A grouping of expenses an employee submits for approval + reimbursement."""

    __tablename__ = "expense_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    # Submitting User (control-plane User id; no cross-DB FK — plain uuid + index).
    employee_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[ExpenseReportStatus] = mapped_column(
        Enum(ExpenseReportStatus, native_enum=False, length=30),
        default=ExpenseReportStatus.draft,
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # --- Money (Numeric, never float) -------------------------------------
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # --- Locked conversion into the ORG REPORTING currency (issue #157) ----
    # ``total_amount`` is denominated in this report's own ``currency``; the CFO
    # threshold (settings.expense_approval.cfo_threshold) is a bare number in the
    # org's reporting currency. These four columns snapshot the total in that
    # currency at SUBMIT time so the gate compares like with like and can't be
    # dodged by filing in a foreign currency. Same shape/semantics as
    # ``invoices.reporting_*`` (migration 0025). NULL = not established; the gate
    # then fails CLOSED (CFO sign-off required). See
    # ``services/expense_currency.py``.
    reporting_currency: Mapped[str | None] = mapped_column(String(3))
    reporting_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    reporting_fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    reporting_fx_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    notes: Mapped[str | None] = mapped_column(Text)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    expenses: Mapped[list["Expense"]] = relationship(back_populates="report")


class CorporateCardTransaction(Base, EntityMixin, TimestampMixin):
    """A corporate / virtual card transaction feed row, reconciled to an expense."""

    __tablename__ = "corporate_card_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    card_ref: Mapped[str | None] = mapped_column(String(255))
    card_last_four: Mapped[str | None] = mapped_column(String(4))
    virtual_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("virtual_cards.id"), index=True
    )
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    posted_date: Mapped[date | None] = mapped_column(Date)
    merchant: Mapped[str | None] = mapped_column(String(255))

    # --- Money (Numeric, never float) -------------------------------------
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Provider transaction id — drives import idempotency (partial unique index).
    external_txn_id: Mapped[str | None] = mapped_column(String(255))
    # Plain FK leg of the expenses↔transactions cycle (the alter side is on
    # Expense.card_transaction_id), so create_all resolves this one inline.
    matched_expense_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expenses.id", name="fk_corporate_card_transactions_matched_expense_id"),
        index=True,
    )
    reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
        Enum(ReconciliationStatus, native_enum=False, length=20),
        default=ReconciliationStatus.unmatched,
        nullable=False,
    )
    import_batch: Mapped[str | None] = mapped_column(String(100))
    raw: Mapped[dict | None] = mapped_column(JSONB)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    __table_args__ = (
        Index(
            "uq_corporate_card_txn_external",
            "organization_id",
            "external_txn_id",
            unique=True,
            postgresql_where=text("external_txn_id IS NOT NULL"),
        ),
    )


class Expense(Base, EntityMixin, TimestampMixin):
    """A single expense line — out-of-pocket or card-funded."""

    __tablename__ = "expenses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Optional — an expense can exist before being grouped into a report.
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_reports.id"), index=True
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    merchant: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)

    # --- Money (Numeric, never float) -------------------------------------
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # --- Locked conversion into the OWNING REPORT's currency (issue #157) --
    # A report legitimately mixes currencies (one trip, several countries), so
    # each line carries its own amount plus this rate-locked expression of it in
    # the report's currency. The rate is locked on the write paths that change
    # what needs converting (create-with-report, amount/currency edit, attach,
    # report-currency change) and read back verbatim — a report's total never
    # drifts with the market. NULL when the line is unattached, or (legacy rows)
    # never locked: a foreign-currency line with no lock is counted as
    # *unconverted* and blocks submission rather than summing at face value.
    # See ``services/expense_currency.py``.
    converted_currency: Mapped[str | None] = mapped_column(String(3))
    converted_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    converted_fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    converted_fx_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    gl_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gl_accounts.id"), index=True
    )
    receipt_file_key: Mapped[str | None] = mapped_column(String(500))
    payment_method: Mapped[ExpensePaymentMethod] = mapped_column(
        Enum(ExpensePaymentMethod, native_enum=False, length=20),
        default=ExpensePaymentMethod.out_of_pocket,
        nullable=False,
    )
    # Alter side of the expenses↔transactions cycle — use_alter so create_all
    # emits this FK as a deferred ALTER once both tables exist.
    card_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "corporate_card_transactions.id",
            use_alter=True,
            name="fk_expenses_card_transaction_id",
        ),
        index=True,
    )
    # List of policy-violation dicts (advisory; no PII).
    policy_violations: Mapped[list | None] = mapped_column(JSONB)
    status: Mapped[ExpenseStatus] = mapped_column(
        Enum(ExpenseStatus, native_enum=False, length=20),
        default=ExpenseStatus.draft,
        nullable=False,
    )
    reimbursable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mileage_miles: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    report: Mapped["ExpenseReport | None"] = relationship(back_populates="expenses")


class ExpensePolicy(Base, EntityMixin, TimestampMixin):
    """A reimbursement policy — per-diem, mileage, category limits, pre-approval
    thresholds. Consumed by later workflows (WF3 policy enforcement)."""

    __tablename__ = "expense_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # NULL = applies to all categories.
    category: Mapped[str | None] = mapped_column(String(100))

    # --- Money / rates (Numeric, never float) -----------------------------
    per_diem_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    per_diem_currency: Mapped[str] = mapped_column(String(3), default="USD")
    mileage_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    category_limit: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    requires_preapproval_above: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    requires_receipt_above: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    rules: Mapped[dict | None] = mapped_column(JSONB)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )


class ExpensePreapproval(Base, EntityMixin, TimestampMixin):
    """A spend pre-approval request raised before an expense is incurred.
    Consumed by later workflows (WF3 pre-approval gating)."""

    __tablename__ = "expense_preapprovals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # --- Money (Numeric, never float) -------------------------------------
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    category: Mapped[str | None] = mapped_column(String(100))
    justification: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PreapprovalStatus] = mapped_column(
        Enum(PreapprovalStatus, native_enum=False, length=20),
        default=PreapprovalStatus.pending,
        nullable=False,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expense_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_reports.id"), index=True
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
