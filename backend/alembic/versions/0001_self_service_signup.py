"""Self-service signup: add User.must_change_password + email_verifications table.

Revision ID: 0001_signup
Revises:
Create Date: 2026-04-19

This is the first Alembic migration in the project. Up to now the schema was
bootstrapped via Base.metadata.create_all() in scripts/seed.py and
scripts/create_tenant.py. To stay compatible with existing dev databases, the
operations in this migration use IF NOT EXISTS so running it against a DB
that already has the columns/tables is a no-op.

Applies ONLY to the control plane DB (account_payables). Tenant DBs are
unaffected — the two new objects live alongside users/orgs/roles.
"""

from alembic import op

revision = "0001_signup"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS email_verifications (
            id UUID PRIMARY KEY,
            token VARCHAR(64) NOT NULL UNIQUE,
            email VARCHAR(320) NOT NULL,
            company_name VARCHAR(255) NOT NULL,
            slug VARCHAR(100) NOT NULL,
            admin_name VARCHAR(255) NOT NULL,
            meta JSONB DEFAULT '{}'::jsonb,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_verifications_token ON email_verifications(token)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_verifications_email ON email_verifications(email)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS email_verifications")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS must_change_password")
