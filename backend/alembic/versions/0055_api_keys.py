"""Programmatic API keys: api_keys table (control plane).

Adds the control-plane ``api_keys`` table backing programmatic (machine-to-
machine) access to the public ``/api/v1`` surface. A key resolves to its
``organization`` and the org resolves to its tenant DB via the existing tenant
chokepoint — so this is a CONTROL-PLANE table keyed by ``organization_id``,
exactly like ``users``; it must NOT fan out to per-tenant DBs.

Revision ID: 0055_api_keys
Revises: 0054_data_subject_requests
Create Date: 2026-06-19

Control-plane DB only (gated on the ``organizations`` table existing, so it
no-ops on tenant DBs — mirrors migration 0021_scim_bearer_hash).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.api_key.ApiKey`` exactly so a control DB built via
``create_all`` (CI / tests) matches a migrated one.
"""

from sqlalchemy import text

from alembic import op

revision = "0055_api_keys"
down_revision = "0054_data_subject_requests"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
    """The control DB is the one with the ``organizations`` table; tenant DBs
    do not have it (mirrors 0021_scim_bearer_hash)."""
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


_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES organizations(id),
        name varchar(120) NOT NULL,
        key_prefix varchar(32) NOT NULL,
        key_hash varchar(64) NOT NULL,
        scopes jsonb NOT NULL DEFAULT '["read"]'::jsonb,
        last_used_at timestamptz,
        revoked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_api_keys_organization_id ON api_keys (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_api_keys_key_prefix ON api_keys (key_prefix)",
]


def upgrade() -> None:
    if not _is_control_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("DROP TABLE IF EXISTS api_keys")
