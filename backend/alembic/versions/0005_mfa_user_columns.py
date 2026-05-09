"""MFA: add User.mfa_secret, mfa_enabled, mfa_enrolled_at columns.

Revision ID: 0005_mfa_user_columns
Revises: 0004_sso_columns
Create Date: 2026-04-19

Per-user TOTP state lives directly on the `users` row. Email-OTP backup codes
live in Redis (short TTL, no need to persist) and org-wide enforcement lives
in `Organization.settings.mfa.required` (JSONB), so neither needs a column.

Control-plane-only — `users` only lives in the control DB.
"""

from sqlalchemy import text

from alembic import op

revision = "0005_mfa_user_columns"
down_revision = "0004_sso_columns"
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

    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_secret VARCHAR(64)")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enrolled_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS mfa_enrolled_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS mfa_enabled")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS mfa_secret")
