"""Index SCIM bearer hash on organizations.

Revision ID: 0021_scim_bearer_hash
Revises: 0020_scheduled_reports
Create Date: 2026-05-18

Control-plane DB only. Adds a dedicated indexed column
``scim_bearer_hash`` on the ``organizations`` table so the SCIM
authentication path can do an indexed lookup instead of scanning
every org row and rebuilding the digest in Python.

The digest is still mirrored into ``settings['sso']['scim_bearer_hash']``
for backward compatibility, but the indexed column is what the
authenticated SCIM request resolves on.
"""

from sqlalchemy import text

from alembic import op

revision = "0021_scim_bearer_hash"
down_revision = "0020_scheduled_reports"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
    """Heuristic: the control DB is the one that has the ``organizations``
    table. Tenant DBs do not."""
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'organizations'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_control_db():
        return
    op.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS scim_bearer_hash VARCHAR(64)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_organizations_scim_bearer_hash "
        "ON organizations (scim_bearer_hash) "
        "WHERE scim_bearer_hash IS NOT NULL"
    )
    # Backfill: pull any existing hash out of settings.sso.scim_bearer_hash.
    op.execute(
        """
        UPDATE organizations
        SET scim_bearer_hash = settings->'sso'->>'scim_bearer_hash'
        WHERE scim_bearer_hash IS NULL
          AND settings->'sso'->>'scim_bearer_hash' IS NOT NULL
        """
    )


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_organizations_scim_bearer_hash")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS scim_bearer_hash")
