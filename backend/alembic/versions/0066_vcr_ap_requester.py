"""AP-initiated vendor change requests: requested_by_user_id (tenant).

Adds support for AP-side bank-detail / tax-id change requests going through the
same dual-control staging table the supplier portal uses. Two changes to
``vendor_change_requests``:

- ``requested_by_user_id`` (nullable UUID) — the control-plane ``User`` who
  staged the change from the AP app. Lets the approve path enforce
  requester != approver (segregation of duties) on AP-initiated requests.
- ``requested_by_vendor_user_id`` made NULLABLE — a portal-submitted request
  fills it; an AP-initiated request leaves it NULL and fills the new column
  instead. Exactly one of the two is set.

Revision ID: 0066_vcr_ap_requester
Revises: 0065_org_parent
Create Date: 2026-06-21

TENANT DB ONLY: ``vendor_change_requests`` is tenant-scoped (it does not exist
on the control-plane DB). The upgrade is gated on the table existing, so the
revision no-ops on the control DB and fans out to every tenant DB via
``scripts/migrate_all_tenants.py`` (or ``FEOH_MIGRATE_TENANT=feoh_<slug> alembic
upgrade head`` for one). Fresh tenants get the shape from ``create_all`` in
``tenant_provisioning`` (it's on the model) — this migration only backfills
existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``
and ``DROP/SET NOT NULL``. No backfill needed — existing rows are all
portal-submitted (their ``requested_by_vendor_user_id`` stays set; the new
column stays NULL).
"""

from sqlalchemy import text

from alembic import op

revision = "0066_vcr_ap_requester"
down_revision = "0065_org_parent"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'vendor_change_requests'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "ALTER TABLE vendor_change_requests ADD COLUMN IF NOT EXISTS requested_by_user_id uuid"
    )
    op.execute(
        "ALTER TABLE vendor_change_requests ALTER COLUMN requested_by_vendor_user_id DROP NOT NULL"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    # Restore NOT NULL only if no AP-initiated (NULL) rows exist, else leave it
    # nullable — re-imposing it would fail against real data.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM vendor_change_requests
                WHERE requested_by_vendor_user_id IS NULL
            ) THEN
                ALTER TABLE vendor_change_requests
                    ALTER COLUMN requested_by_vendor_user_id SET NOT NULL;
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE vendor_change_requests DROP COLUMN IF EXISTS requested_by_user_id")
