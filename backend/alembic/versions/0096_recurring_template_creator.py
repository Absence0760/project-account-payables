"""Record which employee authored a recurring-invoice template (tenant).

Closes the segregation-of-duties gap on a sweep-generated invoice.

The hole
--------
``approval_chain.violates_segregation`` returns False — no breach — when
``Invoice.uploaded_by_id`` is NULL, because NULL is supposed to mean "no
employee created this row". The background recurring sweep has no human actor,
so ``recurring_invoices.generate_one`` stamped NULL, and the generated invoice
became approvable by anyone — including the employee whose own template raised
it.

That NULL reading was wrong for this one path. Email intake, inbound PEPPOL and
the supplier portal genuinely have no control-plane user to record; a recurring
template *does* — an employee wrote the standing instruction that creates the
payable. We simply had nowhere to record them.
``recurring_invoice_templates.created_by_user_id`` is that place, stamped by
``POST /api/recurring``, and ``generate_one`` falls back to it whenever no live
actor triggered the generation.

One column, nullable, no backfill: we cannot know who authored a pre-existing
template, and guessing manufactures either a refusal (blocking an innocent
approver) or an absolution (clearing a guilty one). Existing rows keep the
legacy NULL reading they have always had; every template created from here on
carries its author.

No ForeignKey: ``users`` lives in the CONTROL plane while this table is
tenant-local — the same placement as ``invoices.uploaded_by_id`` and
``vendor_users.provisioned_by_user_id``.

Revision ID: 0096_recurring_template_creator
Revises: 0095_vendor_user_provisioned_by
Create Date: 2026-09-09

The revision id is deliberately shorter than the column name it adds:
``alembic_version.version_num`` is ``VARCHAR(32)`` and
``0096_recurring_template_created_by`` is 34 characters, which would abort
``alembic upgrade head`` before applying anything (see
``tests/test_alembic_revision_ids.py``, and migration 0086 which shipped that
bug). The filename matches the revision id so the two cannot drift.

TENANT DB ONLY: ``recurring_invoice_templates`` is tenant-scoped — it is not in
``tenant_provisioning.CONTROL_TABLES``, so it does not exist on the
control-plane DB. The upgrade is gated on the table existing, so the revision
no-ops on the control DB and fans out to every tenant DB via
``scripts/migrate_all_tenants.py`` (or ``FEOH_MIGRATE_TENANT=feoh_<slug> alembic
upgrade head`` for one). Fresh tenants get the shape from ``create_all`` in
``tenant_provisioning`` (the column is on the model) — this migration only
backfills the shape into existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0096_recurring_template_creator"
down_revision = "0095_vendor_user_provisioned_by"
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
    if _has_table("recurring_invoice_templates"):
        op.execute(
            "ALTER TABLE recurring_invoice_templates "
            "ADD COLUMN IF NOT EXISTS created_by_user_id uuid"
        )


def downgrade() -> None:
    if _has_table("recurring_invoice_templates"):
        op.execute(
            "ALTER TABLE recurring_invoice_templates DROP COLUMN IF EXISTS created_by_user_id"
        )
