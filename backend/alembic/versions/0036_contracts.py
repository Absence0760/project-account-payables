"""Contract management: contracts + contract_line_items tables (tenant-scoped).

Revision ID: 0036_contracts
Revises: 0035_peppol_check_constraints
Create Date: 2026-06-13

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.contract.Contract`` / ``ContractLineItem`` (incl. the
``entity_id`` column from ``EntityMixin``) so a fresh tenant built via
``tenant_provisioning._create_tenant_tables`` (``create_all``) matches a
migrated one.
"""

from sqlalchemy import text

from alembic import op

revision = "0036_contracts"
down_revision = "0035_peppol_check_constraints"
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
    CREATE TABLE IF NOT EXISTS contracts (
        id uuid PRIMARY KEY,
        contract_number varchar(100) NOT NULL,
        title varchar(255),
        description text,
        contract_type varchar(30) NOT NULL DEFAULT 'purchase',
        status varchar(30) NOT NULL DEFAULT 'draft',
        vendor_id uuid NOT NULL REFERENCES vendors(id),
        currency varchar(3) NOT NULL DEFAULT 'USD',
        total_value numeric(15, 2),
        spend_limit numeric(15, 2),
        not_to_exceed boolean NOT NULL DEFAULT false,
        start_date date,
        end_date date,
        signed_date date,
        auto_renew boolean NOT NULL DEFAULT false,
        renewal_term_months integer,
        renewal_notice_days integer NOT NULL DEFAULT 30,
        renewal_alert_sent_at timestamptz,
        payment_terms varchar(100),
        owner_user_id uuid,
        file_url varchar(1024),
        file_key varchar(512),
        terms jsonb,
        meta jsonb,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_contracts_vendor_id ON contracts (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_contracts_end_date ON contracts (end_date)",
    "CREATE INDEX IF NOT EXISTS ix_contracts_organization_id ON contracts (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_contracts_entity_id ON contracts (entity_id)",
    """
    CREATE TABLE IF NOT EXISTS contract_line_items (
        id uuid PRIMARY KEY,
        contract_id uuid NOT NULL REFERENCES contracts(id),
        line_number integer,
        item_code varchar(100),
        description text,
        quantity numeric(12, 4),
        unit_price numeric(15, 2),
        total numeric(15, 2),
        gl_account varchar(100),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_contract_line_items_contract_id "
    "ON contract_line_items (contract_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS contract_line_items")
    op.execute("DROP TABLE IF EXISTS contracts")
