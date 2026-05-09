"""Add payment lifecycle columns for adapter-driven execution.

Revision ID: 0007_payment_lifecycle
Revises: 0006_po_match
Create Date: 2026-04-19

Tenant-only — `payments` lives in tenant DBs. New columns:

- `provider` — which adapter handled the payment (mock, modern_treasury, …)
- `provider_payment_id` — processor's tracking ID; webhook handler looks
  up by this column
- `failure_reason` — populated on `failed` / `cancelled` for debugging
- `submitted_at` — when the adapter accepted the payment
- `completed_at` — when the terminal status (`completed` / `failed`) was
  reported

The pre-existing `reference` column stays — it now holds whatever
human-readable identifier the processor returns (ACH trace number, check
number, etc.). `provider_payment_id` is the processor's internal ID we
use for lookups.
"""

from sqlalchemy import text

from alembic import op

revision = "0007_payment_lifecycle"
down_revision = "0006_po_match"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'payments'"
        )
    ).scalar()
    return result is not None


def upgrade() -> None:
    if not _is_tenant_db():
        return

    op.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider VARCHAR(50)")
    op.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider_payment_id VARCHAR(255)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payments_provider_payment_id "
        "ON payments(provider_payment_id) "
        "WHERE provider_payment_id IS NOT NULL"
    )
    op.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS failure_reason TEXT")
    op.execute(
        "ALTER TABLE payments ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "ALTER TABLE payments ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_payments_provider_payment_id")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS completed_at")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS submitted_at")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS failure_reason")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS provider_payment_id")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS provider")
