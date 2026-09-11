"""Segregation of duties keys on a SET of implicated actors (tenant).

Closes the other half of the recurring-template gap migration 0096 opened.

The hole
--------
0096 gave a template its author (``created_by_user_id``) and
``recurring_invoices.generate_one`` stamps it onto the generated invoice's
``uploaded_by_id``, so the employee whose standing instruction raised a payable
can no longer approve it. An ``ap_manager`` who PATCHes *someone else's*
template — repointing the vendor and the amount — shapes the payable just as
completely, was recorded nowhere on it, and could still approve what it
generated.

Recording the editor in ``created_by_user_id`` instead would only have moved the
exemption to the author: no single column holds both people. So segregation now
keys on a set.

Two columns, both tenant-scoped, both nullable JSONB arrays of stringified
control-plane user ids:

* ``recurring_invoice_templates.material_editor_ids`` — everyone who changed a
  *term* of the payable (vendor, amount, currency, GL coding, PO, payment terms,
  cadence, entity — ``models/recurring_invoice.MATERIAL_EDIT_FIELDS``).
  ``PATCH /api/recurring/{id}`` appends the actor when, and only when, such a
  field actually changes value; a cosmetic rename implicates nobody.
* ``invoices.segregation_actor_ids`` — the actors implicated in this payable
  *besides* the one in ``uploaded_by_id``. ``generate_one`` stamps author ∪
  material editors, minus whoever already landed in ``uploaded_by_id``;
  ``approval_chain.violates_segregation`` refuses an approval by anyone named
  there.

JSONB, not ``uuid[]``: these are sets read as a whole and never joined or
indexed, the codebase's other list-shaped control columns (``invoices.warnings``,
``invoices.po_match``) are JSONB, and ``users`` is control-plane so a real array
of FKs was never on the table anyway (same reason 0096 and 0095 carry no FK).

NULL / empty means "nobody beyond the uploader", which is what keeps the
fail-open NULL-uploader reading intact for the three genuinely actor-less
ingestion channels — email intake, inbound PEPPOL, and supplier-portal submit /
PO flip. Failing closed there was re-rejected for the same reason as in 0096: it
is an outage across three channels, not a control.

No backfill, for decisions §141's reason
----------------------------------------
``updated_at`` records *that* a template was edited, never who, and the audit log
of a ``recurring_template.updated`` row does not say whether the fields it lists
were material under a classification that did not exist when it was written.
Every available proxy — the last updater, the org admin — manufactures either a
*refusal* (a real approver newly blocked on evidence nobody produced) or an
*absolution* (a fabricated name standing in the trail as the responsible
employee). A fraud control's inputs must be observed, not inferred. Edits made
before this migration therefore implicate nobody; every edit from here on does.
Invoices generated before it keep a NULL set, which reads exactly as it did
before the column existed.

Revision ID: 0097_segregation_actor_set
Revises: 0096_recurring_template_creator
Create Date: 2026-09-11

The revision id is 26 characters. ``alembic_version.version_num`` is
``VARCHAR(32)``, and a longer id aborts ``alembic upgrade head`` before applying
anything — migration 0086 shipped exactly that bug and 0096's docstring had to
cite it. ``tests/test_alembic_revision_ids.py`` is the guard; the filename
matches the revision id so the two cannot drift (the trap 0094 set).

TENANT DB ONLY: neither ``recurring_invoice_templates`` nor ``invoices`` is in
``tenant_provisioning.CONTROL_TABLES``, so neither exists on the control-plane
DB. Both statements are gated on the table existing, so the revision no-ops on
the control DB and fans out to every tenant DB via
``scripts/migrate_all_tenants.py`` (or ``FEOH_MIGRATE_TENANT=feoh_<slug> alembic
upgrade head`` for one). Fresh tenants get the shape from ``create_all`` in
``tenant_provisioning`` (both columns are on the models) — this migration only
backfills the shape into existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0097_segregation_actor_set"
down_revision = "0096_recurring_template_creator"
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
            "ADD COLUMN IF NOT EXISTS material_editor_ids jsonb"
        )
    if _has_table("invoices"):
        op.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS segregation_actor_ids jsonb")


def downgrade() -> None:
    if _has_table("invoices"):
        op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS segregation_actor_ids")
    if _has_table("recurring_invoice_templates"):
        op.execute(
            "ALTER TABLE recurring_invoice_templates DROP COLUMN IF EXISTS material_editor_ids"
        )
