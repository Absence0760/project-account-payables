"""Record which AP actor provisioned a supplier-portal credential (tenant).

Closes the fail-open half of the vendor bank-change dual control (BEC gate).

The hole
--------
``approve_change_request`` refused an approval only when
``vendor_change_requests.requested_by_user_id`` equalled the approver — and that
column is NULL for every portal-submitted request. One ``ap_manager`` (who by
default holds ``vendor.manage``, ``vendor.bank_change.approve`` *and*
``payment.execute``) could therefore run the whole redirect alone: invite a
portal user at an address they control, sign in as it, stage a bank change, and
approve their own request through the NULL short-circuit.

Two columns, both nullable, no backfill:

- ``vendor_users.provisioned_by_user_id`` — the control-plane ``User`` who last
  minted a password for this portal identity (invite, or admin password reset).
  NULL for a credential no AP actor has ever held.
- ``vendor_change_requests.requester_provisioned_by_user_id`` — that same value,
  frozen onto the request at staging time so deleting the portal user afterwards
  cannot erase the evidence. The approve path compares THIS against the
  approver.

No backfill is possible or wanted: we cannot know who provisioned a pre-existing
credential, and NULL is the correct, permissive reading — an AP actor cannot
sign in as an identity whose password they never minted, and both routes that
mint one now stamp the column.

Revision ID: 0095_vendor_user_provisioned_by
Revises: 0094_drop_redundant_positive_pay_index
Create Date: 2026-09-09

TENANT DB ONLY: ``vendor_users`` and ``vendor_change_requests`` are tenant-scoped
(neither exists on the control-plane DB). The upgrade is gated on the table
existing, so the revision no-ops on the control DB and fans out to every tenant
DB via ``scripts/migrate_all_tenants.py`` (or
``FEOH_MIGRATE_TENANT=feoh_<slug> alembic upgrade head`` for one). Fresh tenants
get the shape from ``create_all`` in ``tenant_provisioning`` (both columns are on
the models) — this migration only backfills the shape into existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0095_vendor_user_provisioned_by"
down_revision = "0094_drop_redundant_positive_pay_index"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": name},
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if _has_table("vendor_users"):
        op.execute("ALTER TABLE vendor_users ADD COLUMN IF NOT EXISTS provisioned_by_user_id uuid")
    if _has_table("vendor_change_requests"):
        op.execute(
            "ALTER TABLE vendor_change_requests "
            "ADD COLUMN IF NOT EXISTS requester_provisioned_by_user_id uuid"
        )


def downgrade() -> None:
    if _has_table("vendor_change_requests"):
        op.execute(
            "ALTER TABLE vendor_change_requests "
            "DROP COLUMN IF EXISTS requester_provisioned_by_user_id"
        )
    if _has_table("vendor_users"):
        op.execute("ALTER TABLE vendor_users DROP COLUMN IF EXISTS provisioned_by_user_id")
