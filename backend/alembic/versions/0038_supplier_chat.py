"""Embedded supplier chat: supplier_chat_threads + supplier_chat_messages.

Revision ID: 0038_supplier_chat
Revises: 0037_invoice_contract_link
Create Date: 2026-06-13

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.supplier_chat.SupplierChatThread`` /
``SupplierChatMessage`` exactly (incl. the ``entity_id`` column from
``EntityMixin`` on the thread, and BOTH the plain ``invoice_id`` index and the
unique index) so a fresh tenant built via
``tenant_provisioning._create_tenant_tables`` (``create_all``) matches a
migrated one.
"""

from sqlalchemy import text

from alembic import op

revision = "0038_supplier_chat"
down_revision = "0037_invoice_contract_link"
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


_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS supplier_chat_threads (
        id uuid PRIMARY KEY,
        invoice_id uuid NOT NULL REFERENCES invoices(id),
        status varchar(20) NOT NULL DEFAULT 'open',
        resolved_at timestamptz,
        resolved_by uuid,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    # Plain index from `index=True` AND the unique index from `__table_args__` —
    # `create_all` emits both, so the migration must too (create_all parity).
    "CREATE INDEX IF NOT EXISTS ix_supplier_chat_threads_invoice_id "
    "ON supplier_chat_threads (invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_supplier_chat_threads_organization_id "
    "ON supplier_chat_threads (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_supplier_chat_threads_entity_id "
    "ON supplier_chat_threads (entity_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_chat_thread_invoice "
    "ON supplier_chat_threads (invoice_id)",
    """
    CREATE TABLE IF NOT EXISTS supplier_chat_messages (
        id uuid PRIMARY KEY,
        thread_id uuid NOT NULL REFERENCES supplier_chat_threads(id),
        author_role varchar(20) NOT NULL,
        author_user_id uuid,
        author_name varchar(255),
        body text NOT NULL,
        mentions jsonb,
        attachments jsonb,
        template_key varchar(50),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_supplier_chat_messages_thread_id "
    "ON supplier_chat_messages (thread_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS supplier_chat_messages")
    op.execute("DROP TABLE IF EXISTS supplier_chat_threads")
