"""Expense management: expense_reports + expenses + expense_policies +
corporate_card_transactions + expense_preapprovals (all tenant-scoped).

Revision ID: 0039_expense_management
Revises: 0038_supplier_chat
Create Date: 2026-06-13

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.expense`` exactly (incl. the ``entity_id`` column from
``EntityMixin`` on every table, every ``index=True`` plain index, and the
partial unique index on ``corporate_card_transactions``) so a fresh tenant
built via ``tenant_provisioning._create_tenant_tables`` (``create_all``)
matches a migrated one.

The expenses ↔ corporate_card_transactions cycle is broken by creating both
tables WITHOUT the two cross-FKs inline, then ``ADD CONSTRAINT`` afterward —
matching ``use_alter=True`` on the ORM side (see app/models/expense.py).
"""

from sqlalchemy import text

from alembic import op

revision = "0039_expense_management"
down_revision = "0038_supplier_chat"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'invoices'"
            )
        ).scalar()
        is not None
    )


_STATEMENTS = [
    # 1. expense_reports -----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS expense_reports (
        id uuid PRIMARY KEY,
        report_number varchar(50) NOT NULL,
        title varchar(255),
        employee_user_id uuid NOT NULL,
        status varchar(30) NOT NULL DEFAULT 'draft',
        submitted_at timestamptz,
        approved_at timestamptz,
        approved_by uuid,
        total_amount numeric(15, 2) NOT NULL DEFAULT 0,
        currency varchar(3) NOT NULL DEFAULT 'USD',
        notes text,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_expense_reports_employee_user_id "
    "ON expense_reports (employee_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_expense_reports_organization_id "
    "ON expense_reports (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_expense_reports_entity_id ON expense_reports (entity_id)",
    # 2. corporate_card_transactions (created before expenses; its self-side
    #    of the cycle, matched_expense_id, is added by ALTER below) ----------
    """
    CREATE TABLE IF NOT EXISTS corporate_card_transactions (
        id uuid PRIMARY KEY,
        card_ref varchar(255),
        card_last_four varchar(4),
        virtual_card_id uuid REFERENCES virtual_cards(id),
        txn_date date NOT NULL,
        posted_date date,
        merchant varchar(255),
        amount numeric(15, 2) NOT NULL,
        currency varchar(3) NOT NULL DEFAULT 'USD',
        external_txn_id varchar(255),
        matched_expense_id uuid,
        reconciliation_status varchar(20) NOT NULL DEFAULT 'unmatched',
        import_batch varchar(100),
        raw jsonb,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_corporate_card_transactions_virtual_card_id "
    "ON corporate_card_transactions (virtual_card_id)",
    "CREATE INDEX IF NOT EXISTS ix_corporate_card_transactions_matched_expense_id "
    "ON corporate_card_transactions (matched_expense_id)",
    "CREATE INDEX IF NOT EXISTS ix_corporate_card_transactions_organization_id "
    "ON corporate_card_transactions (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_corporate_card_transactions_entity_id "
    "ON corporate_card_transactions (entity_id)",
    # Import idempotency: one row per (org, provider txn id). Partial so rows
    # with NULL external_txn_id (manual entries) don't collide.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_corporate_card_txn_external "
    "ON corporate_card_transactions (organization_id, external_txn_id) "
    "WHERE external_txn_id IS NOT NULL",
    # 3. expenses (its card_transaction_id FK added by ALTER below) ----------
    """
    CREATE TABLE IF NOT EXISTS expenses (
        id uuid PRIMARY KEY,
        report_id uuid REFERENCES expense_reports(id),
        expense_date date NOT NULL,
        merchant varchar(255),
        category varchar(100),
        description text,
        amount numeric(15, 2) NOT NULL,
        currency varchar(3) NOT NULL DEFAULT 'USD',
        gl_account_id uuid REFERENCES gl_accounts(id),
        receipt_file_key varchar(500),
        payment_method varchar(20) NOT NULL DEFAULT 'out_of_pocket',
        card_transaction_id uuid,
        policy_violations jsonb,
        status varchar(20) NOT NULL DEFAULT 'draft',
        reimbursable boolean NOT NULL DEFAULT true,
        mileage_miles numeric(10, 2),
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_expenses_report_id ON expenses (report_id)",
    "CREATE INDEX IF NOT EXISTS ix_expenses_gl_account_id ON expenses (gl_account_id)",
    "CREATE INDEX IF NOT EXISTS ix_expenses_card_transaction_id ON expenses (card_transaction_id)",
    "CREATE INDEX IF NOT EXISTS ix_expenses_organization_id ON expenses (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_expenses_entity_id ON expenses (entity_id)",
    # --- Cross-FKs that close the cycle (idempotent guards via DO blocks) ---
    """
    DO $$ BEGIN
        ALTER TABLE expenses
            ADD CONSTRAINT fk_expenses_card_transaction_id
            FOREIGN KEY (card_transaction_id)
            REFERENCES corporate_card_transactions(id);
    EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """,
    """
    DO $$ BEGIN
        ALTER TABLE corporate_card_transactions
            ADD CONSTRAINT fk_corporate_card_transactions_matched_expense_id
            FOREIGN KEY (matched_expense_id)
            REFERENCES expenses(id);
    EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """,
    # 4. expense_policies ----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS expense_policies (
        id uuid PRIMARY KEY,
        name varchar(255) NOT NULL,
        active boolean NOT NULL DEFAULT true,
        category varchar(100),
        per_diem_amount numeric(15, 2),
        per_diem_currency varchar(3) NOT NULL DEFAULT 'USD',
        mileage_rate numeric(10, 4),
        category_limit numeric(15, 2),
        requires_preapproval_above numeric(15, 2),
        requires_receipt_above numeric(15, 2),
        rules jsonb,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_expense_policies_organization_id "
    "ON expense_policies (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_expense_policies_entity_id ON expense_policies (entity_id)",
    # 5. expense_preapprovals ------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS expense_preapprovals (
        id uuid PRIMARY KEY,
        requester_user_id uuid NOT NULL,
        title varchar(255) NOT NULL,
        estimated_amount numeric(15, 2) NOT NULL,
        currency varchar(3) NOT NULL DEFAULT 'USD',
        category varchar(100),
        justification text,
        status varchar(20) NOT NULL DEFAULT 'pending',
        decided_by uuid,
        decided_at timestamptz,
        expense_report_id uuid REFERENCES expense_reports(id),
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_expense_preapprovals_requester_user_id "
    "ON expense_preapprovals (requester_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_expense_preapprovals_expense_report_id "
    "ON expense_preapprovals (expense_report_id)",
    "CREATE INDEX IF NOT EXISTS ix_expense_preapprovals_organization_id "
    "ON expense_preapprovals (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_expense_preapprovals_entity_id "
    "ON expense_preapprovals (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    # Drop the cycle-closing FKs first so the table drops don't deadlock on
    # the mutual dependency, then drop in reverse dependency order.
    op.execute(
        "ALTER TABLE IF EXISTS corporate_card_transactions "
        "DROP CONSTRAINT IF EXISTS fk_corporate_card_transactions_matched_expense_id"
    )
    op.execute(
        "ALTER TABLE IF EXISTS expenses DROP CONSTRAINT IF EXISTS fk_expenses_card_transaction_id"
    )
    op.execute("DROP TABLE IF EXISTS expense_preapprovals")
    op.execute("DROP TABLE IF EXISTS expense_policies")
    op.execute("DROP TABLE IF EXISTS expenses")
    op.execute("DROP TABLE IF EXISTS corporate_card_transactions")
    op.execute("DROP TABLE IF EXISTS expense_reports")
