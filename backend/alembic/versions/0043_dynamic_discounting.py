"""Dynamic discounting — ``discount_offers`` table (supplier-offered,
sliding-scale early-payment discount offers with an accept/decline/capture
lifecycle).

Revision ID: 0043_dynamic_discounting
Revises: 0042_vendor_risk_screening
Create Date: 2026-06-16

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.discount.DiscountOffer`` exactly so a fresh tenant built
via ``tenant_provisioning._create_tenant_tables`` (``create_all``) matches a
migrated one. See ``backend/docs/dynamic-discounting.md``.
"""

from sqlalchemy import text

from alembic import op

revision = "0043_dynamic_discounting"
down_revision = "0042_vendor_risk_screening"
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


_UPGRADE = [
    """
    CREATE TABLE IF NOT EXISTS discount_offers (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        scope varchar(20) NOT NULL DEFAULT 'invoice',
        invoice_id uuid REFERENCES invoices(id),
        vendor_id uuid REFERENCES vendors(id),
        source varchar(20) NOT NULL DEFAULT 'supplier',
        status varchar(20) NOT NULL DEFAULT 'offered',
        tiers jsonb NOT NULL DEFAULT '[]'::jsonb,
        base_amount numeric(15, 2) NOT NULL,
        currency varchar(3) NOT NULL DEFAULT 'USD',
        valid_from date,
        valid_until date,
        accepted_tier jsonb,
        accepted_at timestamptz,
        accepted_by uuid,
        captured_amount numeric(15, 2),
        captured_at timestamptz,
        financing_provider varchar(50),
        notes varchar(500),
        meta jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_discount_offers_organization_id "
    "ON discount_offers (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_discount_offers_entity_id ON discount_offers (entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_discount_offers_invoice_id ON discount_offers (invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_discount_offers_vendor_id ON discount_offers (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_discount_offers_status ON discount_offers (status)",
]

_DOWNGRADE = [
    "DROP TABLE IF EXISTS discount_offers",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    bind = op.get_bind()
    for stmt in _UPGRADE:
        bind.execute(text(stmt))


def downgrade() -> None:
    if not _is_tenant_db():
        return
    bind = op.get_bind()
    for stmt in _DOWNGRADE:
        bind.execute(text(stmt))
