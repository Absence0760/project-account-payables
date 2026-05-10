"""Exception queue: assignment + SLA columns.

Revision ID: 0013_exception_assignment_sla
Revises: 0012_credit_memos
Create Date: 2026-05-10

Tenant DB only. Adds columns on ``exceptions`` so the queue can:
- Route to a specific user via ``assigned_to_user_id`` (FK to control-
  plane User UUIDs — kept as a plain UUID, no FK enforcement at the
  DB level since the user table lives in the control plane).
- Compute SLA: ``due_at`` is the deadline derived from the org's
  per-type SLA at creation time. ``time_to_resolution_seconds`` is
  populated when the exception flips to a terminal state.

The existing free-text ``assigned_to`` column stays for backward
compat — the API surfaces both, but new code should rely on the UUID.
"""

from sqlalchemy import text

from alembic import op

revision = "0013_exception_assignment_sla"
down_revision = "0012_credit_memos"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'exceptions'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "ALTER TABLE exceptions "
        "ADD COLUMN IF NOT EXISTS assigned_to_user_id UUID, "
        "ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ, "
        "ADD COLUMN IF NOT EXISTS time_to_resolution_seconds INTEGER"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_exceptions_assigned_to_user_id "
        "ON exceptions (assigned_to_user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_exceptions_due_at "
        "ON exceptions (due_at) WHERE status IN ('open', 'escalated')"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_exceptions_assigned_to_user_id")
    op.execute("DROP INDEX IF EXISTS ix_exceptions_due_at")
    op.execute(
        "ALTER TABLE exceptions "
        "DROP COLUMN IF EXISTS assigned_to_user_id, "
        "DROP COLUMN IF EXISTS due_at, "
        "DROP COLUMN IF EXISTS time_to_resolution_seconds"
    )
