"""Platform billing & metering: plans + subscriptions tables (control plane).

Adds the control-plane ``plans`` and ``subscriptions`` tables backing the
platform billing model (sellable tier + per-org subscription lifecycle). Both
are keyed off ``organizations`` — they are CONTROL-PLANE tables, exactly like
``users`` / ``api_keys`` / ``assistant_usage``, and must NOT fan out to
per-tenant DBs.

Revision ID: 0056_platform_billing
Revises: 0055_api_keys
Create Date: 2026-06-19

Control-plane DB only (gated on the ``organizations`` table existing, so it
no-ops on tenant DBs — mirrors migration 0055_api_keys / 0021_scim_bearer_hash).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.billing`` exactly so a control DB built via ``create_all``
(CI / tests) matches a migrated one.
"""

from sqlalchemy import text

from alembic import op

revision = "0056_platform_billing"
down_revision = "0055_api_keys"
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
    CREATE TABLE IF NOT EXISTS plans (
        id uuid PRIMARY KEY,
        code varchar(50) NOT NULL,
        name varchar(120) NOT NULL,
        monthly_price numeric(12, 2) NOT NULL DEFAULT 0.00,
        currency varchar(3) NOT NULL DEFAULT 'USD',
        seat_component jsonb NOT NULL DEFAULT '{}'::jsonb,
        usage_components jsonb NOT NULL DEFAULT '{}'::jsonb,
        entitlements jsonb NOT NULL DEFAULT '{}'::jsonb,
        trial_days integer NOT NULL DEFAULT 0,
        is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_plans_code ON plans (code)",
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES organizations(id),
        plan_id uuid NOT NULL REFERENCES plans(id),
        status varchar(20) NOT NULL DEFAULT 'trialing',
        current_period_start timestamptz,
        current_period_end timestamptz,
        trial_end timestamptz,
        external_subscription_id varchar(255),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_subscription_org_plan UNIQUE (organization_id, plan_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_subscriptions_organization_id "
    "ON subscriptions (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_subscriptions_plan_id ON subscriptions (plan_id)",
    # At most one LIVE subscription per org. A canceled row is kept for history,
    # so the uniqueness is partial. Mirrors the PEPPOL one-live-per-invoice idea.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_one_live_per_org "
    "ON subscriptions (organization_id) WHERE status <> 'canceled'",
]


def upgrade() -> None:
    if not _is_control_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("DROP TABLE IF EXISTS subscriptions")
    op.execute("DROP TABLE IF EXISTS plans")
