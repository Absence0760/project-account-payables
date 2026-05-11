"""Bank reconciliation: statements + transactions + payment match.

Revision ID: 0019_bank_reconciliation
Revises: 0018_kyc_compliance
Create Date: 2026-05-10

Tenant DB only. Two new tables that let an org upload a bank
statement (CSV / OFX export from their bank) and auto-match the
debits against the Payment rows we issued on the same period.

  - ``bank_statements`` — one row per uploaded file. Stores the
    account it covers, the period boundaries, the source format
    (`csv` / `ofx` / `camt053`), and the actor who uploaded.

  - ``bank_transactions`` — one row per parsed line. Links back to
    its statement via FK, and optionally to a `Payment` row via
    `matched_payment_id` when the reconciliation matcher finds a
    fit. The match carries metadata: confidence (0–100), the
    `match_method` we used (`provider_id`, `amount_date`,
    `fuzzy_vendor`), and the raw transaction blob for replay.

  - Unmatched transactions get `matched_payment_id=NULL`; the AP
    queue surfaces them via a separate exception type.

Indexes:
  - `(statement_id, transaction_date)` for the statement detail page
  - `(matched_payment_id)` for the "did we match this payment?" join
  - `(organization_id, transaction_date DESC)` for cross-statement
    timeline views (cash-flow dashboard)
"""

from sqlalchemy import text

from alembic import op

revision = "0019_bank_reconciliation"
down_revision = "0018_kyc_compliance"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'payments'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bank_statements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL,
            account_identifier VARCHAR(80) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'USD',
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            source_format VARCHAR(20) NOT NULL,
            file_key VARCHAR(512),
            imported_by UUID,
            opening_balance NUMERIC(18, 2),
            closing_balance NUMERIC(18, 2),
            transaction_count INTEGER NOT NULL DEFAULT 0,
            matched_count INTEGER NOT NULL DEFAULT 0,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bank_transactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            statement_id UUID NOT NULL REFERENCES bank_statements(id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            transaction_date DATE NOT NULL,
            posted_date DATE,
            amount NUMERIC(18, 2) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'USD',
            description VARCHAR(500),
            counterparty_name VARCHAR(255),
            reference VARCHAR(255),
            direction VARCHAR(10) NOT NULL,  -- 'debit' or 'credit'
            raw_data JSONB,
            matched_payment_id UUID REFERENCES payments(id) ON DELETE SET NULL,
            match_method VARCHAR(40),
            match_confidence NUMERIC(5, 2),
            matched_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bank_transactions_statement "
        "ON bank_transactions (statement_id, transaction_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bank_transactions_matched_payment "
        "ON bank_transactions (matched_payment_id) WHERE matched_payment_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bank_transactions_org_date "
        "ON bank_transactions (organization_id, transaction_date DESC)"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_bank_transactions_org_date")
    op.execute("DROP INDEX IF EXISTS ix_bank_transactions_matched_payment")
    op.execute("DROP INDEX IF EXISTS ix_bank_transactions_statement")
    op.execute("DROP TABLE IF EXISTS bank_transactions")
    op.execute("DROP TABLE IF EXISTS bank_statements")
