"""Conversational AP Assistant tables.

Branch-aware (mirrors the dual-plane pattern used by prior revisions): gated on
the presence of the ``invoices`` table.

  - **Tenant DB** (``invoices`` present): ``assistant_conversations`` +
    ``assistant_messages`` (conversation content is tenant data — must inherit
    tenant isolation). Fans out via
    ``FEOH_MIGRATE_TENANT=ap_acme alembic upgrade head`` then
    ``python scripts/migrate_all_tenants.py``. Fresh tenants get these via
    ``tenant_provisioning._create_tenant_tables`` (``create_all``) once the
    models are registered.

  - **Control plane** (no ``invoices``): ``assistant_usage`` (per-org/month
    token meter — billing is a control-plane concern, next to
    ``extraction_usage``). Applied via plain ``alembic upgrade head``.
    ``assistant_usage`` is in ``tenant_provisioning.CONTROL_TABLES`` so it is
    never created on a tenant DB.

Idempotent: ``CREATE TABLE/INDEX IF NOT EXISTS``. Mirrors
``app.models.assistant`` so a fresh ``create_all`` tenant matches a migrated one.
See ``docs/conversational-assistant.md``.
"""

from sqlalchemy import text

from alembic import op

revision = "0032_assistant"
down_revision = "0031_workflow_suggestions"
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


_TENANT_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS assistant_conversations (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        user_id uuid NOT NULL,
        title varchar(255),
        meta jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_assistant_conversations_organization_id "
    "ON assistant_conversations (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_assistant_conversations_user_id "
    "ON assistant_conversations (user_id)",
    """
    CREATE TABLE IF NOT EXISTS assistant_messages (
        id uuid PRIMARY KEY,
        conversation_id uuid NOT NULL
            REFERENCES assistant_conversations(id) ON DELETE CASCADE,
        role varchar(20) NOT NULL,
        content text NOT NULL,
        tool_calls jsonb NOT NULL DEFAULT '{}'::jsonb,
        input_tokens integer NOT NULL DEFAULT 0,
        output_tokens integer NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_assistant_messages_conversation_id "
    "ON assistant_messages (conversation_id)",
]

_CONTROL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS assistant_usage (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        period varchar(7) NOT NULL,
        input_tokens integer NOT NULL DEFAULT 0,
        output_tokens integer NOT NULL DEFAULT 0,
        request_count integer NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_assistant_usage_org_period UNIQUE (organization_id, period)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_assistant_usage_organization_id "
    "ON assistant_usage (organization_id)",
]


def upgrade() -> None:
    statements = _TENANT_STATEMENTS if _is_tenant_db() else _CONTROL_STATEMENTS
    for stmt in statements:
        op.execute(stmt)


def downgrade() -> None:
    if _is_tenant_db():
        op.execute("DROP TABLE IF EXISTS assistant_messages")
        op.execute("DROP TABLE IF EXISTS assistant_conversations")
    else:
        op.execute("DROP TABLE IF EXISTS assistant_usage")
