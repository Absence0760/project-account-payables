"""Perf: index the vendor-scoped history lookup + duplicate-detection check on
``invoices``, plus every unindexed ``invoice_id`` FK that hangs off it.

``services.invoice_warnings.refresh_warnings`` runs on EVERY invoice save
(extraction, correction, status transition) and issues several queries with no
supporting index:

- The always-on duplicate-detection gate filters on
  ``lower(trim(invoice_number)) = ...`` — a plain btree index can't serve a
  wrapped-column predicate, so this was a full-table seq scan on every save.
- The bank-change / stat-anomaly / price-variance vendor-history lookups all
  filter ``vendor_id = ... AND status IN (...)`` ordered by
  ``created_at DESC LIMIT N`` — also a full-table seq scan with no index on
  either ``vendor_id`` or ``status``.

Measured against a 1.2M-row ``ap_acme`` (~24k invoices/vendor, a realistic
multi-year AP volume): each of these was a ~100-310ms Parallel Seq Scan
touching ~33k buffers — and ``refresh_warnings`` fires 3-4 of them serially on
a single invoice save. `ix_invoices_vendor_id_created_at` turns the
vendor-history lookups into an index scan bounded by ``LIMIT`` (status stays a
cheap in-index filter since vendor_id already narrows to one vendor's rows and
the created_at order lets Postgres stop early);
`ix_invoices_invoice_number_norm` mirrors the exact
``lower(trim(invoice_number))`` expression used by the duplicate check as a
functional index.

While tracking down why a cleanup DELETE against the inflated ``invoices``
table was pathologically slow, the same investigation turned up seven more
tables FK'ing to ``invoices.id`` with **no index at all** on that column —
Postgres does not auto-index FK columns, and a DELETE (or any equality lookup)
against the parent must otherwise seq-scan every one of these to check for
referencing rows. `exceptions.invoice_id` is the sharpest case:
``invoice_warnings._ensure_exception`` runs an existence-check SELECT against
it up to ~10x per single invoice save (once per fraud/warning rule that
fires). The rest (`invoice_line_items`, `invoice_extraction_results`,
`payment_schedules`, `payments`, `vendor_statement_recon_lines.
matched_invoice_id`, `workflow_instances`) are hit on nearly every invoice
detail read or mutation. Same root cause (missing FK index), same mechanical
fix — added here rather than left as a dangling follow-up.

Revision ID: 0072_invoice_perf_indexes
Revises: 0071_report_definitions
Create Date: 2026-07-01

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py`` — or
``AP_MIGRATE_TENANT=ap_<slug> alembic upgrade head`` for one). Fresh tenants
get the shape from ``create_all`` in
``tenant_provisioning._create_tenant_tables`` (every index here is declared on
the owning model — ``Invoice.__table_args__``, or ``index=True`` on the
respective ``invoice_id`` / ``matched_invoice_id`` column); this migration
only builds them for existing tenants.

Idempotent: ``CREATE INDEX IF NOT EXISTS`` / ``DROP INDEX IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0072_invoice_perf_indexes"
down_revision = "0071_report_definitions"
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
    "CREATE INDEX IF NOT EXISTS ix_invoices_vendor_id_created_at "
    "ON invoices (vendor_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_invoices_invoice_number_norm "
    "ON invoices (lower(trim(invoice_number)))",
    # Unindexed invoice_id FKs turned up while diagnosing the same slow-scan
    # class of problem — see the module docstring.
    "CREATE INDEX IF NOT EXISTS ix_exceptions_invoice_id ON exceptions (invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_invoice_line_items_invoice_id "
    "ON invoice_line_items (invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_invoice_extraction_results_invoice_id "
    "ON invoice_extraction_results (invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_payment_schedules_invoice_id ON payment_schedules (invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_payments_invoice_id ON payments (invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_statement_recon_lines_matched_invoice_id "
    "ON vendor_statement_recon_lines (matched_invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_instances_invoice_id "
    "ON workflow_instances (invoice_id)",
]

_DOWNGRADE = [
    "DROP INDEX IF EXISTS ix_workflow_instances_invoice_id",
    "DROP INDEX IF EXISTS ix_vendor_statement_recon_lines_matched_invoice_id",
    "DROP INDEX IF EXISTS ix_payments_invoice_id",
    "DROP INDEX IF EXISTS ix_payment_schedules_invoice_id",
    "DROP INDEX IF EXISTS ix_invoice_extraction_results_invoice_id",
    "DROP INDEX IF EXISTS ix_invoice_line_items_invoice_id",
    "DROP INDEX IF EXISTS ix_exceptions_invoice_id",
    "DROP INDEX IF EXISTS ix_invoices_invoice_number_norm",
    "DROP INDEX IF EXISTS ix_invoices_vendor_id_created_at",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _DOWNGRADE:
        op.execute(stmt)
