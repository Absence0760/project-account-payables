"""Procurement / Requisitions: catalogs + catalog_items + budgets +
purchase_requisitions + requisition_line_items + intake_requests (all
tenant-scoped).

Revision ID: 0041_procurement
Revises: 0040_workflow_versions
Create Date: 2026-06-13

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.procurement`` exactly (incl. the ``entity_id`` column from
``EntityMixin`` on every table except ``requisition_line_items``, and every
``index=True`` plain index) so a fresh tenant built via
``tenant_provisioning._create_tenant_tables`` (``create_all``) matches a
migrated one.

No circular FKs: tables are created in dependency order — catalogs →
catalog_items → budgets → purchase_requisitions → requisition_line_items →
intake_requests (purchase_orders / contracts / vendors / gl_accounts / entities
already exist).
"""

from sqlalchemy import text

from alembic import op

revision = "0041_procurement"
down_revision = "0040_workflow_versions"
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
    # 1. catalogs ------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS catalogs (
        id uuid PRIMARY KEY,
        name varchar(255) NOT NULL,
        catalog_type varchar(20) NOT NULL DEFAULT 'internal',
        vendor_id uuid REFERENCES vendors(id),
        punchout_url varchar(500),
        is_active boolean NOT NULL DEFAULT true,
        is_preferred boolean NOT NULL DEFAULT false,
        description text,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_catalogs_vendor_id ON catalogs (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_catalogs_organization_id ON catalogs (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_catalogs_entity_id ON catalogs (entity_id)",
    # 2. catalog_items -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS catalog_items (
        id uuid PRIMARY KEY,
        catalog_id uuid NOT NULL REFERENCES catalogs(id),
        sku varchar(100),
        name varchar(255) NOT NULL,
        description text,
        unit_price numeric(15, 2),
        currency varchar(3) NOT NULL DEFAULT 'USD',
        uom varchar(20),
        vendor_id uuid REFERENCES vendors(id),
        gl_account_id uuid REFERENCES gl_accounts(id),
        category varchar(100),
        is_active boolean NOT NULL DEFAULT true,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_catalog_items_catalog_id ON catalog_items (catalog_id)",
    "CREATE INDEX IF NOT EXISTS ix_catalog_items_vendor_id ON catalog_items (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_catalog_items_gl_account_id ON catalog_items (gl_account_id)",
    "CREATE INDEX IF NOT EXISTS ix_catalog_items_organization_id "
    "ON catalog_items (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_catalog_items_entity_id ON catalog_items (entity_id)",
    # 3. budgets -------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS budgets (
        id uuid PRIMARY KEY,
        name varchar(255) NOT NULL,
        dimension varchar(20) NOT NULL DEFAULT 'department',
        dimension_value varchar(150) NOT NULL,
        period varchar(20),
        period_start date,
        period_end date,
        amount numeric(15, 2) NOT NULL,
        currency varchar(3) NOT NULL DEFAULT 'USD',
        notes text,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_budgets_organization_id ON budgets (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_budgets_entity_id ON budgets (entity_id)",
    # 4. purchase_requisitions ----------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS purchase_requisitions (
        id uuid PRIMARY KEY,
        requisition_number varchar(50) NOT NULL,
        title varchar(255),
        requester_user_id uuid NOT NULL,
        department varchar(120),
        status varchar(30) NOT NULL DEFAULT 'draft',
        needed_by date,
        justification text,
        vendor_id uuid REFERENCES vendors(id),
        contract_id uuid REFERENCES contracts(id),
        budget_id uuid REFERENCES budgets(id),
        total numeric(15, 2) NOT NULL DEFAULT 0,
        currency varchar(3) NOT NULL DEFAULT 'USD',
        notes text,
        submitted_at timestamptz,
        approved_at timestamptz,
        approved_by uuid,
        rejection_reason text,
        converted_po_id uuid REFERENCES purchase_orders(id),
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_purchase_requisitions_requester_user_id "
    "ON purchase_requisitions (requester_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_purchase_requisitions_vendor_id "
    "ON purchase_requisitions (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_purchase_requisitions_contract_id "
    "ON purchase_requisitions (contract_id)",
    "CREATE INDEX IF NOT EXISTS ix_purchase_requisitions_budget_id "
    "ON purchase_requisitions (budget_id)",
    "CREATE INDEX IF NOT EXISTS ix_purchase_requisitions_converted_po_id "
    "ON purchase_requisitions (converted_po_id)",
    "CREATE INDEX IF NOT EXISTS ix_purchase_requisitions_organization_id "
    "ON purchase_requisitions (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_purchase_requisitions_entity_id "
    "ON purchase_requisitions (entity_id)",
    # 5. requisition_line_items (no EntityMixin — child of requisition) ------
    """
    CREATE TABLE IF NOT EXISTS requisition_line_items (
        id uuid PRIMARY KEY,
        requisition_id uuid NOT NULL REFERENCES purchase_requisitions(id),
        line_number integer,
        catalog_item_id uuid REFERENCES catalog_items(id),
        item_code varchar(100),
        description text,
        quantity numeric(12, 4),
        unit_price numeric(15, 2),
        total numeric(15, 2),
        gl_account_id uuid REFERENCES gl_accounts(id),
        uom varchar(20),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_requisition_line_items_requisition_id "
    "ON requisition_line_items (requisition_id)",
    "CREATE INDEX IF NOT EXISTS ix_requisition_line_items_catalog_item_id "
    "ON requisition_line_items (catalog_item_id)",
    "CREATE INDEX IF NOT EXISTS ix_requisition_line_items_gl_account_id "
    "ON requisition_line_items (gl_account_id)",
    # 6. intake_requests -----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS intake_requests (
        id uuid PRIMARY KEY,
        request_number varchar(50) NOT NULL,
        title varchar(255) NOT NULL,
        request_type varchar(20) NOT NULL DEFAULT 'other',
        requester_user_id uuid NOT NULL,
        description text,
        estimated_amount numeric(15, 2),
        currency varchar(3) NOT NULL DEFAULT 'USD',
        vendor_name varchar(255),
        vendor_id uuid REFERENCES vendors(id),
        status varchar(20) NOT NULL DEFAULT 'open',
        form_data jsonb,
        needed_by date,
        justification text,
        converted_requisition_id uuid REFERENCES purchase_requisitions(id),
        converted_po_id uuid REFERENCES purchase_orders(id),
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_intake_requests_requester_user_id "
    "ON intake_requests (requester_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_intake_requests_vendor_id ON intake_requests (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_intake_requests_converted_requisition_id "
    "ON intake_requests (converted_requisition_id)",
    "CREATE INDEX IF NOT EXISTS ix_intake_requests_converted_po_id "
    "ON intake_requests (converted_po_id)",
    "CREATE INDEX IF NOT EXISTS ix_intake_requests_organization_id "
    "ON intake_requests (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_intake_requests_entity_id ON intake_requests (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    # Reverse dependency order.
    op.execute("DROP TABLE IF EXISTS intake_requests")
    op.execute("DROP TABLE IF EXISTS requisition_line_items")
    op.execute("DROP TABLE IF EXISTS purchase_requisitions")
    op.execute("DROP TABLE IF EXISTS budgets")
    op.execute("DROP TABLE IF EXISTS catalog_items")
    op.execute("DROP TABLE IF EXISTS catalogs")
