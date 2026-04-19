"""SSO: backfill User.sso_provider and User.sso_provider_id columns.

Revision ID: 0004_sso_user_columns
Revises: 0003_rag_embeddings
Create Date: 2026-04-19

The columns were added to the SQLAlchemy model in the same change that wired
up OIDC, but Base.metadata.create_all() in the bootstrap scripts only creates
tables that don't exist — it doesn't ADD columns to tables that already do.
This migration backfills them on existing dev DBs. IF NOT EXISTS makes it a
no-op when the columns are already present.

Control-plane-only — `users` only lives in the control DB.
"""

from sqlalchemy import text

from alembic import op

revision = "0004_sso_columns"
down_revision = "0003_rag"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'users'"
        )
    ).scalar()
    return result is not None


def upgrade() -> None:
    if not _is_control_db():
        return

    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS sso_provider VARCHAR(50)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS sso_provider_id VARCHAR(255)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_sso_lookup "
        "ON users (sso_provider, sso_provider_id) "
        "WHERE sso_provider IS NOT NULL"
    )


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_users_sso_lookup")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS sso_provider_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS sso_provider")
