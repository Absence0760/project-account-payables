"""International payments: FX + corridor columns on payments.

Revision ID: 0017_international_payments
Revises: 0016_card_reveal_tokens
Create Date: 2026-05-10

Tenant DB only. Extends ``payments`` with the bookkeeping a
cross-currency or cross-border payment needs:

  - ``source_currency``, ``source_amount``: what we *paid out from*
    (the org's home currency leg). When equal to the invoice's
    currency the payment is domestic and source_amount = amount.
  - ``fx_rate``, ``fx_locked_at``: the rate snapshotted at the moment
    of submission. Persisting both means an audit can replay exactly
    what the customer agreed to.
  - ``corridor``: the chosen rails (``ach``, ``sepa``, ``international_wire``,
    etc.) so reporting can break spend down by corridor without
    re-deriving from method+currency.
  - ``target_country``: ISO 3166-1 alpha-2. Carried separately from
    the vendor's address so a vendor that moves jurisdictions
    doesn't rewrite history on past payments.

All new columns are nullable / default 0 / default '' — backfill is
unnecessary because domestic same-currency payments don't need them
populated.
"""

from sqlalchemy import text

from alembic import op

revision = "0017_international_payments"
down_revision = "0016_card_reveal_tokens"
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
        ALTER TABLE payments
            ADD COLUMN IF NOT EXISTS source_currency VARCHAR(3),
            ADD COLUMN IF NOT EXISTS source_amount NUMERIC(15, 2),
            ADD COLUMN IF NOT EXISTS fx_rate NUMERIC(18, 8),
            ADD COLUMN IF NOT EXISTS fx_locked_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS corridor VARCHAR(40),
            ADD COLUMN IF NOT EXISTS target_country VARCHAR(2)
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payments_corridor "
        "ON payments (corridor) WHERE corridor IS NOT NULL"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_payments_corridor")
    op.execute(
        """
        ALTER TABLE payments
            DROP COLUMN IF EXISTS source_currency,
            DROP COLUMN IF EXISTS source_amount,
            DROP COLUMN IF EXISTS fx_rate,
            DROP COLUMN IF EXISTS fx_locked_at,
            DROP COLUMN IF EXISTS corridor,
            DROP COLUMN IF EXISTS target_country
        """
    )
