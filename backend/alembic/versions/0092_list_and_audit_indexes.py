"""Index the two query shapes that had no index at all: the ``audit_log``
sweeps, and every list endpoint's own default sort order.

Two open follow-ups, one revision, because they are the same defect — a query
the app runs constantly, against a table that only grows, with nothing behind
it but a sequential scan.

1. ``audit_log`` — the fastest-growing table in the schema — carried indexes on
   ``correlation_id`` and ``organization_id`` only. Nothing on ``created_at`` or
   ``action``, so every one of these seq-scanned the whole table:

   - ``GET /api/audit/verify-signatures`` (``api/audit.py``) — the SOX
     population-level non-repudiation sweep: ``WHERE action = 'invoice.approved'
     AND created_at >= :start AND created_at < :end ORDER BY created_at``.
   - ``GET /api/audit/export`` (``api/audit.py``) — the auditor's date-range
     export: ``WHERE created_at >= … AND created_at < … ORDER BY created_at``.
   - and three more that share those predicates: the dashboard's
     approval-timing leg (``api/dashboard.py``, ``entity_type='invoice' AND
     action='invoice.approved'`` — on every dashboard load), the adaptive
     feedback reads (``api/adaptive_workflows.py``, ``action IN (…) AND
     created_at >= :since``), and the retention sweep's overdue-row count
     (``services/retention_sweep.py``, ``created_at < :cutoff``, and
     ``created_at < :cutoff AND shipped_at IS NULL`` — the exact predicate
     ``ix_audit_log_shipped_at_null`` is built on).

   The shipper's own query — ``WHERE shipped_at IS NULL ORDER BY created_at ASC
   LIMIT n``, **per tenant, every 60 s, forever** — turns out to have HAD an
   index all along, and this revision creates nothing new for it. Migration
   ``0010_audit_log_shipping`` added ``ix_audit_log_shipped_at_null`` in exactly
   that shape. What was missing is the MODEL declaration: fresh tenants are
   built by ``create_all`` in ``tenant_provisioning``, never by Alembic, so a
   tenant provisioned that way never had the index and ran the 60-second sweep
   as a full sequential scan of its largest table — the migrated-vs-provisioned
   drift this revision's test now makes impossible. It is fixed by declaring the
   index on ``AuditLog.__table_args__``; the CREATE is restated here (in
   ``_ADOPTED``, idempotent) only to catch an already-provisioned tenant that
   reaches this revision, and is deliberately NOT in the downgrade — reverting
   this revision must not remove revision 0010's index.

2. Seven list endpoints ordered by a column with no index, so page 1 of every
   list view was a whole-table read plus a top-N heapsort — a cost that grows
   with the table while the page size stays at 20. ``invoices``, ``payments``,
   ``exceptions``, ``expenses``, ``purchase_orders`` and ``contracts`` all
   default to ``created_at DESC, id DESC``; ``corporate_card_transactions``
   sorts by ``txn_date DESC`` instead, so it gets that column, not
   ``created_at`` — the "same shape" is not the same column, and an index on
   the wrong one would never be read.

   Each table gets a plain ordering index (the unfiltered view) and a
   status-leading composite (the status chips). The composite is not redundant:
   with only the plain index a *rare, scattered* status makes Postgres walk the
   ordering index discarding non-matching rows until it has 20 — 18 928 rows
   discarded for one page in the measurement below — and that discard count
   grows linearly with the table.

Measured, not assumed
---------------------
Local Postgres 16, warm cache, ``EXPLAIN (ANALYZE, BUFFERS)``, third run of
each query. Dataset built for this migration: **1 200 000 ``audit_log`` rows
spanning 2 years** of which 40 000 (3.3 %) are ``invoice.approved``;
**200 000 rows each** in ``invoices`` / ``payments`` / ``exceptions`` /
``expenses`` / ``corporate_card_transactions``; **50 000 each** in
``purchase_orders`` / ``contracts``.

``audit_log`` (1.2 M rows):

| query (rows returned)                    | before                 | after            |
|------------------------------------------|------------------------|------------------|
| shipper, caught up (0) [0010's index]    | 39.740 ms / 30 003 buf | 0.040 ms / 1     |
| verify-signatures, 30-day range (1 643)  | 41.010 ms / 36 590 buf | 4.198 ms / 6 643 |
| verify-signatures, 1-year range (19 999) | 57.672 ms / 35 039 buf | 19.089 ms / 5 697|
| audit export, 7-day range (11 500)       | 64.427 ms / 30 076 buf | 1.381 ms / 1 091 |
| audit export, 90-day range (147 871)     | 98.467 ms / 30 076 buf | 16.115 ms /14 016|
|   (before also spilled a 9.5 MB sort)    |                        | (no spill)       |
| dashboard approval-timing leg (40 000)   | 50.603 ms / 30 000 buf | 11.301 ms / 1 244|
| adaptive feedback, 90 days (15 141)      | 56.282 ms / 30 006 buf | 16.406 ms /13 719|
| retention overdue count (0)              | 52.335 ms / 30 000 buf | 0.028 ms / 3     |

List endpoints, page 1 (``LIMIT 20``):

| list (rows in table)                  | before                 | after                |
|---------------------------------------|------------------------|----------------------|
| invoices, no filter (200 k)           | 15.780 ms / 4 952 buf  | 0.031 ms / 4 buf     |
| invoices, entity-scoped (200 k)       | 23.816 ms / 4 952 buf  | 0.036 ms / 4 buf     |
| invoices, common status 10 % (200 k)  | 13.068 ms / 4 952 buf  | 0.053 ms / 8 buf     |
| invoices, rare status 0.1 % (200 k)   | 2.145 ms / 591 buf *   | 0.036 ms / 4 buf     |
| invoices, OFFSET 1000 (200 k)         | 32.349 ms / 4 952 buf  | 0.141 ms / 33 buf    |
| payments, status filter (200 k)       | 8.598 ms / 3 169 buf   | 0.030 ms / 5 buf     |
| payments, no filter (200 k)           | 13.677 ms / 3 169 buf  | 0.026 ms / 4 buf     |
| exceptions, status filter (200 k)     | 9.070 ms / 3 922 buf   | 0.024 ms / 5 buf     |
| expenses, status filter (200 k)       | 10.818 ms / 4 014 buf  | 0.032 ms / 5 buf     |
| purchase_orders, status (50 k)        | 4.188 ms / 821 buf     | 0.021 ms / 4 buf     |
| contracts, status filter (50 k)       | 4.359 ms / 1 026 buf   | 0.028 ms / 4 buf     |
| corporate_card_transactions (200 k)   | 9.698 ms / 3 525 buf   | 0.044 ms / 62 buf    |

\\* the rare-status row is measured with the plain ordering index already in
place and the status composite dropped — that is what the composite buys, and
why it is a separate index rather than dead weight.

Plans move from Parallel Seq Scan + top-N heapsort to a plain Index Scan the
``LIMIT`` stops early, so the fixed version stays flat as the table grows.

Measured NOT to help, and deliberately excluded
-----------------------------------------------
- ``GET /api/invoices/counts`` (the status chips): a rollup over 100 % of the
  table. 17.440 ms before / 15.837 ms after — the planner keeps a seq scan,
  correctly. Same conclusion migration 0090 reached for the budget rollup.
- The list's own ``SELECT count(*)`` total: 15.003 ms / 13.618 ms. It counts
  the whole filtered population, so no ordering index can bound it.
- The ``search=`` path: ``ILIKE '%term%'`` has a leading wildcard, which no
  btree index can serve. Only ``pg_trgm`` would, at its own write cost, and
  that is a separate decision, not a free rider on this one.

Index cost at the measured volumes: ``ix_audit_log_action_created_at`` 50 MB
and ``ix_audit_log_created_at`` 26 MB against a 282 MB table;
``ix_audit_log_shipped_at_null`` is **8 KB**, because a partial index only holds
the unshipped tail. The list indexes are ~8 MB (plain) / ~11 MB (composite) per
200 k rows against 40-86 MB tables.

Why NOT ``CREATE INDEX CONCURRENTLY``
-------------------------------------
Considered and rejected, deliberately. ``CONCURRENTLY`` avoids the
``ACCESS EXCLUSIVE`` lock a plain ``CREATE INDEX`` holds for its duration, which
is a real production consideration on a table this size — but it cannot run
inside a transaction, so it would need Alembic's ``autocommit_block()``, and
inside that block nothing rolls back. The decisive problem is that
``CONCURRENTLY`` and ``IF NOT EXISTS`` compose into a trap: a concurrent build
that fails or is cancelled leaves an **INVALID** index behind, and the next run
of this same migration sees the name exists, skips it, and reports success — a
permanently non-functional index that nothing in the system detects. Plain
``CREATE INDEX`` inside the migration's own transaction gives real rollback and
no invalid-index state.

The lock window is bounded and measured: on the dataset above the whole
revision builds in ~2.5 s across all eight tables, the single longest index
being ``ix_audit_log_action_created_at`` at 742 ms on 1.2 M rows. An operator
with an ``audit_log`` large enough that this matters has a documented escape
hatch: build the indexes by hand with ``CONCURRENTLY``, verify
``pg_index.indisvalid`` on each, then run the migration — every statement here
is ``IF NOT EXISTS``, so it becomes a no-op. That is an operator decision with
a verification step, not a hazard hidden inside the migration.

Revision ID: 0092_list_and_audit_indexes
Revises: 0091_webauthn_cred_rp_id
Create Date: 2026-09-06

TENANT DB ONLY: all eight tables are tenant-scoped (none is in
``tenant_provisioning.CONTROL_TABLES``). Each statement is gated on its own
table existing, so the revision no-ops on the control plane and fans out to
every tenant DB via ``scripts/migrate_all_tenants.py`` (or
``FEOH_MIGRATE_TENANT=feoh_<slug> alembic upgrade head`` for one). Fresh tenants
get every index from ``create_all`` in ``tenant_provisioning`` — each one is
declared in the owning model's ``__table_args__`` — so a migrated tenant and a
freshly-provisioned one end up identical. ``tests/test_list_and_audit_indexes.py``
is the guard on both halves.

Idempotent + reversible: ``CREATE INDEX IF NOT EXISTS`` / ``DROP INDEX IF
EXISTS``.

See ``backend/docs/database.md`` § Index coverage on list + audit reads and
``backend/docs/audit-log-shipping.md`` § Index coverage.
"""

from sqlalchemy import text

from alembic import op

revision = "0092_list_and_audit_indexes"
down_revision = "0091_webauthn_cred_rp_id"
branch_labels = None
depends_on = None


#: (table, index name, index body) triples — the body is everything after
#: ``ON``. Upgrade and downgrade are both generated from this ONE declaration,
#: so a renamed index can't leave a stale ``DROP`` behind. The table name gates
#: the statement, so a tenant DB that predates one of these tables — and the
#: control plane, which has none of them — is skipped rather than erroring.
_INDEXES: list[tuple[str, str, str]] = [
    # --- audit_log -------------------------------------------------------
    # `action` leads because every consumer filters it to equality (or a small
    # IN list) and then ranges/orders on `created_at`: the signature sweep, the
    # dashboard approval-timing leg, the adaptive feedback reads.
    (
        "audit_log",
        "ix_audit_log_action_created_at",
        "ON audit_log (action, created_at)",
    ),
    # The auditor's date-range export orders by `created_at` with no action
    # predicate, so it cannot use the composite above. Also what turns the
    # export's external-merge disk sort into an ordered index scan.
    (
        "audit_log",
        "ix_audit_log_created_at",
        "ON audit_log (created_at)",
    ),
    # --- list endpoints: default sort order ------------------------------
    (
        "invoices",
        "ix_invoices_created_at_id",
        "ON invoices (created_at DESC, id DESC)",
    ),
    (
        "invoices",
        "ix_invoices_status_created_at_id",
        "ON invoices (status, created_at DESC, id DESC)",
    ),
    (
        "payments",
        "ix_payments_created_at_id",
        "ON payments (created_at DESC, id DESC)",
    ),
    (
        "payments",
        "ix_payments_status_created_at_id",
        "ON payments (status, created_at DESC, id DESC)",
    ),
    (
        "exceptions",
        "ix_exceptions_created_at_id",
        "ON exceptions (created_at DESC, id DESC)",
    ),
    (
        "exceptions",
        "ix_exceptions_status_created_at_id",
        "ON exceptions (status, created_at DESC, id DESC)",
    ),
    (
        "expenses",
        "ix_expenses_created_at_id",
        "ON expenses (created_at DESC, id DESC)",
    ),
    (
        "expenses",
        "ix_expenses_status_created_at_id",
        "ON expenses (status, created_at DESC, id DESC)",
    ),
    (
        "purchase_orders",
        "ix_purchase_orders_created_at_id",
        "ON purchase_orders (created_at DESC, id DESC)",
    ),
    (
        "purchase_orders",
        "ix_purchase_orders_status_created_at_id",
        "ON purchase_orders (status, created_at DESC, id DESC)",
    ),
    (
        "contracts",
        "ix_contracts_created_at_id",
        "ON contracts (created_at DESC, id DESC)",
    ),
    (
        "contracts",
        "ix_contracts_status_created_at_id",
        "ON contracts (status, created_at DESC, id DESC)",
    ),
    # This list sorts by `txn_date`, not `created_at` (the card feed is filed
    # under the date the charge happened, not the date we imported it), and
    # filters on `reconciliation_status`. Same shape, different columns.
    (
        "corporate_card_transactions",
        "ix_corp_card_txn_date_id",
        "ON corporate_card_transactions (txn_date DESC, id DESC)",
    ),
    (
        "corporate_card_transactions",
        "ix_corp_card_recon_status_txn_date_id",
        "ON corporate_card_transactions (reconciliation_status, txn_date DESC, id DESC)",
    ),
]

#: Indexes this revision ENSURES EXIST but does not OWN. The shipper's partial
#: index was already declared by migration `0010_audit_log_shipping` — under the
#: name kept here, which is why nothing new is created for it. What was missing
#: is the MODEL declaration: fresh tenants are built by `create_all` in
#: `tenant_provisioning`, never by Alembic, so every tenant provisioned that way
#: has been running the 60-second shipper sweep against no index at all while a
#: migrated tenant had one. Adding it to `AuditLog.__table_args__` closes that;
#: restating the CREATE here (idempotent) catches an already-provisioned tenant
#: that reaches this revision. It is deliberately absent from `_DOWNGRADE`:
#: reverting THIS revision must not remove an index revision 0010 installed.
_ADOPTED: list[tuple[str, str, str]] = [
    (
        "audit_log",
        "ix_audit_log_shipped_at_null",
        "ON audit_log (created_at) WHERE shipped_at IS NULL",
    ),
]

_UPGRADE: list[tuple[str, str]] = [
    (table, f"CREATE INDEX IF NOT EXISTS {name} {body}")
    for table, name, body in _ADOPTED + _INDEXES
]

_DOWNGRADE: list[tuple[str, str]] = [
    (table, f"DROP INDEX IF EXISTS {name}") for table, name, _body in reversed(_INDEXES)
]


def _table_exists(name: str) -> bool:
    return (
        op.get_bind()
        .execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :name"
            ),
            {"name": name},
        )
        .scalar()
        is not None
    )


def _run(statements: list[tuple[str, str]]) -> None:
    seen: dict[str, bool] = {}
    for table, stmt in statements:
        if table not in seen:
            seen[table] = _table_exists(table)
        if seen[table]:
            op.execute(stmt)


def upgrade() -> None:
    _run(_UPGRADE)


def downgrade() -> None:
    _run(_DOWNGRADE)
