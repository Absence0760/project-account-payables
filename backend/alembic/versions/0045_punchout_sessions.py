"""Punch-out sessions: live cXML/OCI punch-out round-trip correlation rows.

Adds the ``punchout_sessions`` table (tenant-scoped) that correlates a started
punch-out session to the eventual returned supplier cart (by ``buyer_cookie``)
and to the requisition it converts into.

Revision ID: 0045_punchout_sessions
Revises: 0044_invoice_department_project
Create Date: 2026-06-17

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.procurement.PunchoutSession`` exactly (incl. the
``entity_id`` column from ``EntityMixin`` and every ``index=True`` plain index,
plus the unique index on ``buyer_cookie``) so a fresh tenant built via
``tenant_provisioning._create_tenant_tables`` (``create_all``) matches a
migrated one.

The FKs (catalogs, purchase_requisitions, entities) all exist by migration 0041.
"""

from sqlalchemy import text

from alembic import op

revision = "0045_punchout_sessions"
down_revision = "0044_invoice_department_project"
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
    CREATE TABLE IF NOT EXISTS punchout_sessions (
        id uuid PRIMARY KEY,
        catalog_id uuid NOT NULL REFERENCES catalogs(id),
        buyer_cookie varchar(80) NOT NULL,
        status varchar(20) NOT NULL DEFAULT 'pending',
        requested_by_user_id uuid NOT NULL,
        start_url varchar(1000),
        provider varchar(40),
        cart_items jsonb,
        cart_total numeric(15, 2),
        currency varchar(3) NOT NULL DEFAULT 'USD',
        returned_at timestamptz,
        converted_requisition_id uuid REFERENCES purchase_requisitions(id),
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    # buyer_cookie is the correlation key the supplier echoes in the cart — the
    # return endpoint matches it to exactly one session, so it is UNIQUE.
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_punchout_sessions_buyer_cookie "
    "ON punchout_sessions (buyer_cookie)",
    "CREATE INDEX IF NOT EXISTS ix_punchout_sessions_catalog_id ON punchout_sessions (catalog_id)",
    "CREATE INDEX IF NOT EXISTS ix_punchout_sessions_requested_by_user_id "
    "ON punchout_sessions (requested_by_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_punchout_sessions_converted_requisition_id "
    "ON punchout_sessions (converted_requisition_id)",
    "CREATE INDEX IF NOT EXISTS ix_punchout_sessions_organization_id "
    "ON punchout_sessions (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_punchout_sessions_entity_id ON punchout_sessions (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS punchout_sessions")
