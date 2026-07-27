"""Perf: index the PO/GR/line-item lookups ``services.po_matching.match_invoice_to_po``
runs on every invoice with a ``po_number``.

Same investigation as migration 0072 (the ``invoice_warnings.refresh_warnings``
hot path), one hop further down: ``_refresh_po_match`` calls
``match_invoice_to_po`` for every invoice with a ``po_number`` that isn't a
draft, which in turn issues queries with no supporting index:

- ``PurchaseOrder.po_number == ... [AND PurchaseOrder.vendor_id == ...]``
  ordered by ``created_at DESC LIMIT 1`` — the PO lookup itself.
- ``GoodsReceipt.po_id == ...`` — the 3-way-match GR fetch.
- ``selectinload(PurchaseOrder.line_items)`` / ``selectinload(GoodsReceipt.
  line_items)`` — ``po_line_items.po_id`` / ``gr_line_items.gr_id``, both
  unindexed FKs (Postgres doesn't auto-index FK columns; same class of bug as
  0072's ``invoice_id`` sweep).

Measured against ``ap_acme`` inflated to 50k purchase_orders / 30k
goods_receipts (a realistic multi-year PO volume): the PO lookup was a 5.1ms
Seq Scan over 50,005 rows; the GR lookup was a 1.8ms Seq Scan over 30,371 rows.
Smaller in absolute terms than the invoices table (POs/GRs are usually an
order of magnitude fewer rows than invoices), but same shape, same fix, same
hot path, and ``vendor_id``/``po_id`` are also filtered directly by
``api/purchase_orders.py``, ``api/goods_receipts.py``, ``api/portal.py``
(supplier-portal PO listing), ``api/enrichment.py``, and the
``missing_po``/``multi_po_split`` exception-agent resolvers.

Revision ID: 0073_po_matching_perf_indexes
Revises: 0072_invoice_perf_indexes
Create Date: 2026-07-01

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py`` — or
``FEOH_MIGRATE_TENANT=ap_<slug> alembic upgrade head`` for one). Fresh tenants
get the shape from ``create_all`` in ``tenant_provisioning._create_tenant_tables``
(every index here is declared ``index=True`` on the owning model in
``app.models.procurement``); this migration only builds them for existing
tenants.

Idempotent: ``CREATE INDEX IF NOT EXISTS`` / ``DROP INDEX IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0073_po_matching_perf_indexes"
down_revision = "0072_invoice_perf_indexes"
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
    "CREATE INDEX IF NOT EXISTS ix_purchase_orders_po_number ON purchase_orders (po_number)",
    "CREATE INDEX IF NOT EXISTS ix_purchase_orders_vendor_id ON purchase_orders (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_goods_receipts_po_id ON goods_receipts (po_id)",
    "CREATE INDEX IF NOT EXISTS ix_po_line_items_po_id ON po_line_items (po_id)",
    "CREATE INDEX IF NOT EXISTS ix_gr_line_items_gr_id ON gr_line_items (gr_id)",
]

_DOWNGRADE = [
    "DROP INDEX IF EXISTS ix_gr_line_items_gr_id",
    "DROP INDEX IF EXISTS ix_po_line_items_po_id",
    "DROP INDEX IF EXISTS ix_goods_receipts_po_id",
    "DROP INDEX IF EXISTS ix_purchase_orders_vendor_id",
    "DROP INDEX IF EXISTS ix_purchase_orders_po_number",
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
