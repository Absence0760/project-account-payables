"""Drop ``ix_positive_pay_files_payment_run_id`` — a redundant prefix of
``uq_positive_pay_run_format``.

Migration 0048 created both indexes on ``positive_pay_files``:

* ``ix_positive_pay_files_payment_run_id`` — plain btree on ``(payment_run_id)``;
* ``uq_positive_pay_run_format`` — UNIQUE on ``(payment_run_id, bank_format)
  WHERE payment_run_id IS NOT NULL``, THE idempotency backstop under the
  check-issue endpoint's read-then-insert (see 0093's docstring).

The first is a leading-column prefix of the second. Every query this table runs
against the column is an equality on a real run id
(``PositivePayFile.payment_run_id == run_id`` in ``api/positive_pay.py``'s
generate + regenerate lookups), and Postgres's predicate prover derives
``payment_run_id IS NOT NULL`` from ``payment_run_id = $1``, so the partial
unique index is usable for all of them — as it is for the FK's own referential
check on a ``payment_runs`` delete, which issues the same equality. Nothing
filters ``payment_run_id IS NULL`` (the ``ach_authorization`` rows the partial
index excludes are selected by ``file_type``, which has its own index), so the
exclusion costs nothing either.

What was left was one extra btree to maintain on every insert and every
return-processing update, buying no read it did not already have. This is
exactly the shape 0093 resolved for ``ix_bank_transactions_matched_payment``,
and it is resolved the same way: drop the redundant index here, drop the
``index=True`` that would otherwise rebuild it on every ``create_all``-provisioned
tenant, and record the reason in
``tests/test_migration_model_index_parity.py::EXEMPT`` so the parity guard reads
it as a decision rather than as the drift it is designed to catch.

``downgrade()`` recreates 0048's index. It deliberately does NOT touch
``uq_positive_pay_run_format`` — 0048 owns that one and 0093 ensures it.

Revision ID: 0094_positive_pay_index_prune
Revises: 0093_migration_only_indexes

(The id is kept short on purpose: ``alembic_version.version_num`` is
``varchar(32)``, so a descriptive-but-long revision id fails on the UPDATE at
the very end of the migration — after its DDL has already run.)
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0094_positive_pay_index_prune"
down_revision = "0093_migration_only_indexes"
branch_labels = None
depends_on = None

_TABLE = "positive_pay_files"
_INDEX = "ix_positive_pay_files_payment_run_id"

#: 0048's spelling, restored verbatim by ``downgrade``.
_RECREATE = f"CREATE INDEX IF NOT EXISTS {_INDEX} ON {_TABLE} (payment_run_id)"


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


def upgrade() -> None:
    # Tenant-only table; the control plane reaches this revision too and simply
    # has nothing to do (same gate 0092 / 0093 use).
    if _table_exists(_TABLE):
        op.execute(f"DROP INDEX IF EXISTS {_INDEX}")


def downgrade() -> None:
    if _table_exists(_TABLE):
        op.execute(_RECREATE)
