"""Per-key API usage meter: api_key_usage table (control plane).

Adds the control-plane ``api_key_usage`` aggregate table — one row per
``(api_key_id, usage_date)`` holding a running request count for that UTC day —
metering programmatic ``/api/v1`` requests authenticated by ``X-API-Key``. It
feeds the platform billing rollup. Keyed off ``api_keys`` / ``organizations``
and resolves through the existing org→tenant chokepoint, exactly like
``api_keys`` (migration 0055): a CONTROL-PLANE table that must NOT fan out to
per-tenant DBs.

Revision ID: 0058_api_key_usage
Revises: 0057_outbound_webhooks
Create Date: 2026-06-20

Control-plane DB only (gated on the ``organizations`` table existing, so it
no-ops on tenant DBs — mirrors migration 0055_api_keys / 0057_outbound_webhooks).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.api_key.ApiKeyUsage`` exactly so a control DB built via
``create_all`` (CI / tests) matches a migrated one.
"""

from sqlalchemy import text

from alembic import op

revision = "0058_api_key_usage"
down_revision = "0057_outbound_webhooks"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
    """The control DB is the one with the ``organizations`` table; tenant DBs
    do not have it (mirrors 0055_api_keys)."""
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
    CREATE TABLE IF NOT EXISTS api_key_usage (
        id uuid PRIMARY KEY,
        api_key_id uuid NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
        organization_id uuid NOT NULL REFERENCES organizations(id),
        usage_date date NOT NULL,
        request_count bigint NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_api_key_usage_api_key_id ON api_key_usage (api_key_id)",
    "CREATE INDEX IF NOT EXISTS ix_api_key_usage_organization_id "
    "ON api_key_usage (organization_id)",
    # One aggregate row per key per day — the upsert (ON CONFLICT) target for
    # the best-effort increment in the auth path.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_key_usage_key_day "
    "ON api_key_usage (api_key_id, usage_date)",
]


def upgrade() -> None:
    if not _is_control_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("DROP TABLE IF EXISTS api_key_usage")
