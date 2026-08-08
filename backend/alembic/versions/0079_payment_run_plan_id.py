"""Cash-Flow Copilot plan_id idempotency anchor on payment_runs (tenant).

Adds a nullable ``plan_id`` column to ``payment_runs`` + a partial unique
index covering every non-NULL value, so retrying
``POST /api/cash-flow/plans/{plan_id}/draft-run`` for the SAME deterministic
plan id (``services/cash_flow_plan.compute_plan_id``) returns the existing
draft run instead of staging a second one. NULL on every run created through
the ordinary ``POST /api/payments/runs`` flow — this column and index only
matter to the AI Cash-Flow Copilot's Phase 3 enact path (draft-only; the run
always lands ``status='draft'`` and is never auto-executed by it). See
docs/cash-flow-copilot.md §5/§6.

Revision ID: 0079_payment_run_plan_id
Revises: 0078_user_device_tokens
Create Date: 2026-08-08

TENANT DB ONLY: ``payment_runs`` is tenant-scoped. The upgrade is gated on
the table existing, so the revision no-ops on the control DB and fans out to
every tenant DB via ``scripts/migrate_all_tenants.py``. Fresh tenants get the
column + index from ``create_all`` in ``tenant_provisioning`` (declared on
the model).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``CREATE UNIQUE INDEX IF NOT
EXISTS`` / their ``DROP`` counterparts.
"""

from sqlalchemy import text

from alembic import op

revision = "0079_payment_run_plan_id"
down_revision = "0078_user_device_tokens"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'payment_runs'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE payment_runs ADD COLUMN IF NOT EXISTS plan_id varchar(64)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_runs_plan_id "
        "ON payment_runs (plan_id) WHERE plan_id IS NOT NULL"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_payment_runs_plan_id")
    op.execute("ALTER TABLE payment_runs DROP COLUMN IF EXISTS plan_id")
