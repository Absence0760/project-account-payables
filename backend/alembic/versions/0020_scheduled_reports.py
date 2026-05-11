"""Scheduled report delivery — per-tenant cron-like schedules.

Revision ID: 0020_scheduled_reports
Revises: 0019_bank_reconciliation
Create Date: 2026-05-10

Tenant DB only. A `scheduled_reports` row says "email this report
to these addresses on this cadence." A background loop sweeps the
table on a timer, picks up the due rows, generates the CSV via
`services/report_export`, and ships it via the configured email
adapter.

Cadence is `daily` | `weekly` | `monthly`. The next_run_at column
is the source of truth for due-ness; the worker updates it after a
successful run (failures leave it alone so the next sweep retries).
"""

from sqlalchemy import text

from alembic import op

revision = "0020_scheduled_reports"
down_revision = "0019_bank_reconciliation"
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


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_reports (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL,
            name VARCHAR(120) NOT NULL,
            report_type VARCHAR(40) NOT NULL,
            cadence VARCHAR(20) NOT NULL,
            recipients JSONB NOT NULL,
            period_days INTEGER NOT NULL DEFAULT 30,
            enabled BOOLEAN NOT NULL DEFAULT true,
            next_run_at TIMESTAMPTZ NOT NULL,
            last_run_at TIMESTAMPTZ,
            last_run_status VARCHAR(20),
            last_run_error VARCHAR(500),
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scheduled_reports_due "
        "ON scheduled_reports (next_run_at) WHERE enabled = true"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_scheduled_reports_due")
    op.execute("DROP TABLE IF EXISTS scheduled_reports")
