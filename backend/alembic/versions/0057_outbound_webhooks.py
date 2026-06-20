"""Outbound webhooks: webhook_subscriptions + webhook_deliveries (control plane).

Adds the two control-plane tables backing outbound webhooks — an org's external
integrator subscribes to platform events (invoice approved, payment settled,
exception raised) at a target URL, and each emitted event becomes a signed,
retried delivery. Both are keyed by ``organization_id`` and resolve through the
existing org→tenant chokepoint, exactly like ``api_keys`` (migration 0055) — so
these are CONTROL-PLANE tables; they must NOT fan out to per-tenant DBs.

Revision ID: 0057_outbound_webhooks
Revises: 0056_platform_billing
Create Date: 2026-06-19

Control-plane DB only (gated on the ``organizations`` table existing, so it
no-ops on tenant DBs — mirrors migration 0055_api_keys).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.webhook`` exactly so a control DB built via ``create_all``
(CI / tests) matches a migrated one.
"""

from sqlalchemy import text

from alembic import op

revision = "0057_outbound_webhooks"
down_revision = "0056_platform_billing"
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
    CREATE TABLE IF NOT EXISTS webhook_subscriptions (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES organizations(id),
        name varchar(120) NOT NULL,
        target_url text NOT NULL,
        event_types jsonb NOT NULL DEFAULT '[]'::jsonb,
        signing_secret varchar(128) NOT NULL,
        secret_prefix varchar(16) NOT NULL,
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_webhook_subscriptions_organization_id "
    "ON webhook_subscriptions (organization_id)",
    """
    CREATE TABLE IF NOT EXISTS webhook_deliveries (
        id uuid PRIMARY KEY,
        subscription_id uuid NOT NULL
            REFERENCES webhook_subscriptions(id) ON DELETE CASCADE,
        organization_id uuid NOT NULL REFERENCES organizations(id),
        event_id varchar(64) NOT NULL,
        event_type varchar(64) NOT NULL,
        payload jsonb NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'pending',
        attempt_count integer NOT NULL DEFAULT 0,
        next_attempt_at timestamptz,
        last_attempt_at timestamptz,
        response_code integer,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_subscription_id "
    "ON webhook_deliveries (subscription_id)",
    "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_organization_id "
    "ON webhook_deliveries (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_status ON webhook_deliveries (status)",
    "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_next_attempt_at "
    "ON webhook_deliveries (next_attempt_at)",
    # Dedupe: one delivery per (subscription, event) — the webhook-discipline
    # guard that a re-fired / replayed event can't queue the same delivery twice.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_webhook_delivery_sub_event "
    "ON webhook_deliveries (subscription_id, event_id)",
]


def upgrade() -> None:
    if not _is_control_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("DROP TABLE IF EXISTS webhook_deliveries")
    op.execute("DROP TABLE IF EXISTS webhook_subscriptions")
