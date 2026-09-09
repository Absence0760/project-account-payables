"""Drop ``ix_positive_pay_files_payment_run_id`` — a redundant prefix of
``uq_positive_pay_run_format``.

The claim
---------
``positive_pay_files`` carries two indexes over the same leading column:

* ``ix_positive_pay_files_payment_run_id`` — ``(payment_run_id)``, created by
  migration 0048 and declared on the model as ``index=True``;
* ``uq_positive_pay_run_format`` — ``(payment_run_id, bank_format) WHERE
  payment_run_id IS NOT NULL``, UNIQUE, and the ONLY concurrency backstop under
  ``POST /api/positive-pay/payment-runs/{id}/check-issue``'s read-then-insert
  (see ``backend/docs/positive-pay.md`` § Idempotency). It cannot be dropped.

A B-tree's leading column is independently searchable, so the composite serves
every read the single-column one could — and serves the table's actual reads
*better*, because both of them qualify on ``bank_format`` too. The narrow index
is therefore pure write overhead: every insert, every ``process-return`` update
that moves ``status``, and every ``DELETE /api/positive-pay/{id}`` maintains a
second B-tree nothing plans against.

Verified, not assumed
---------------------
On a 55 000-row scratch copy of the table (50 000 run-scoped rows + 5 000
run-less ``ach_authorization`` rows), Postgres 16, ``ANALYZE``d, with the narrow
index dropped:

* ``payment_run_id = $1 AND bank_format = $2`` (both real call sites — the
  idempotency lookup at ``check-issue`` and its post-``IntegrityError`` re-read)
  -> ``Index Scan using uq_positive_pay_run_format``, with BOTH columns in the
  ``Index Cond``. With the narrow index present the planner chose it instead and
  pushed ``bank_format`` down to a ``Filter``, so this is a strictly better plan,
  not merely an equal one.
* ``payment_run_id = $1`` alone -> ``Index Scan using
  uq_positive_pay_run_format``. A partial index is usable here because
  ``x = $1`` is strict, so any row satisfying it also satisfies
  ``payment_run_id IS NOT NULL``.
* The referential-integrity probe the FK to ``payment_runs`` issues when a
  parent run is deleted (``SELECT 1 FROM positive_pay_files WHERE
  payment_run_id = $1 FOR KEY SHARE``) -> ``Index Scan using
  uq_positive_pay_run_format``. The FK check only ever looks for a non-NULL id,
  which is exactly the partial index's own predicate.

The one thing the composite does NOT serve
------------------------------------------
``WHERE payment_run_id IS NULL`` — the partial predicate excludes those rows
entirely, so that lookup becomes a sequential scan. **No such query exists.**
The two run-scoped call sites both bind a concrete run id; the only other
reference to the column is ``cast(payment_run_id AS text) ILIKE '%term%'`` in
the list endpoint's search, which no B-tree on the raw column could serve
either. The ``ach_authorization`` (run-less) rows are reached through
``file_type``, which has its own index. If a "list the run-less files" query is
ever added, this decision has to be revisited — that is the trigger, and it is
why the drop is reasoned out here rather than done silently.

Why the model change ships in the same commit
---------------------------------------------
A database in this project is built two ways and only one of them runs Alembic:
an existing tenant gets ``alembic upgrade head``, a NEW one gets
``Base.metadata.create_all`` from the ORM
(``services/tenant_provisioning._create_tenant_tables``). A migration-only drop
would therefore reach half the fleet — and every freshly-provisioned tenant
would keep re-creating the index this revision removes. So
``PositivePayFile.payment_run_id`` loses its ``index=True`` in the same commit,
and ``tests/test_migration_model_index_parity.py`` gains the matching ``EXEMPT``
entry (0048 still creates it, so the parity guard needs the written reason).
Same lesson migration 0093 wrote up; see ``docs/decisions.md`` §104 / §109.

Revision ID: 0094_drop_redundant_ppf_index
Revises: 0093_migration_only_indexes
Create Date: 2026-09-08

TENANT DATABASES. ``positive_pay_files`` is tenant-scoped, so this revision
no-ops on the control plane (the statement is gated on the table existing) and
does its work under ``scripts/migrate_all_tenants.py``.

Idempotent + reversible: ``DROP INDEX IF EXISTS`` / ``CREATE INDEX IF NOT
EXISTS``. Reverting restores exactly what migration 0048 built.

Why NOT ``DROP INDEX CONCURRENTLY``: same reasoning as 0092 / 0093 —
``CONCURRENTLY`` cannot run inside Alembic's transaction. A ``DROP`` takes a
brief ``ACCESS EXCLUSIVE`` lock on a table written only by an operator
generating a Positive Pay file, so the blocking form is safe here.

See ``backend/docs/positive-pay.md`` § Indexes.
"""

from sqlalchemy import text

from alembic import op

revision = "0094_drop_redundant_ppf_index"
down_revision = "0093_migration_only_indexes"
branch_labels = None
depends_on = None

_TABLE = "positive_pay_files"
_INDEX = "ix_positive_pay_files_payment_run_id"

#: Restores migration 0048's spelling verbatim, so a downgrade leaves the
#: database in exactly the shape 0093 left it.
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
    if _table_exists(_TABLE):
        op.execute(f"DROP INDEX IF EXISTS {_INDEX}")


def downgrade() -> None:
    if _table_exists(_TABLE):
        op.execute(_RECREATE)
