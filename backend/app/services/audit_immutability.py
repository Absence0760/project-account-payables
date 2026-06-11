"""DB-level immutability guard for the tenant ``audit_log`` table.

SOX requires the audit trail be tamper-evident: once a row is written it must
not be silently UPDATEd or DELETEd. The app already enforces "no PATCH/DELETE
route" (``tests/test_audit_append_only.py``), but that is necessary, not
sufficient — a rogue ORM call or a direct ``psql`` session would bypass it.

The durable control is a pair of ``BEFORE`` triggers on ``audit_log`` that
``RAISE EXCEPTION`` on any DELETE and on any UPDATE that changes a column other
than ``shipped_at``. The ``shipped_at`` carve-out is required: the centralized
audit-log shipper (``services/audit_log_shipper.py``) legitimately stamps
``shipped_at`` after a batch reaches every WORM sink — that single column is the
only mutation the trail permits.

The DDL lives here (not just in the Alembic revision) so it can be applied to
*every* tenant DB, including those created via ``create_all`` in
``tenant_provisioning._create_tenant_tables`` (new tenants + the test harness),
not only those upgraded through Alembic. Both paths call
``install_audit_immutability``; the migration is the production fan-out, this
helper keeps fresh tenants consistent.

All statements are idempotent (``CREATE OR REPLACE`` / ``DROP ... IF EXISTS``)
so applying twice is a no-op.
"""

from __future__ import annotations

# Statements are kept SEPARATE (not one multi-command string) because asyncpg
# prepares each statement and refuses "multiple commands in a prepared
# statement". The migration runs them via psycopg (which would accept a blob)
# but the tenant-provisioning path runs over asyncpg, so both consume the list.
#
# The trigger function rejects every DELETE outright and every UPDATE that
# changes any column other than ``shipped_at``. The UPDATE check compares the
# full set of non-``shipped_at`` columns between OLD and NEW; if any differ the
# update is blocked, so re-stamping ``shipped_at`` alongside another edit is
# also rejected. A pure ``shipped_at`` stamp (the shipper's only write) passes.
_CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_log_block_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'audit_log is append-only: DELETE is not permitted';
    END IF;
    -- UPDATE: permit only a shipped_at-only change (the shipper's stamp).
    IF (NEW.id, NEW.correlation_id, NEW.organization_id, NEW.actor_id,
        NEW.action, NEW.entity_type, NEW.entity_id, NEW.details, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.correlation_id, OLD.organization_id, OLD.actor_id,
        OLD.action, OLD.entity_type, OLD.entity_id, OLD.details, OLD.created_at)
    THEN
        RAISE EXCEPTION
            'audit_log is append-only: only shipped_at may be updated';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""".strip()

_INSTALL_STATEMENTS: tuple[str, ...] = (
    _CREATE_FUNCTION,
    "DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log;",
    (
        "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutation();"
    ),
    "DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;",
    (
        "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutation();"
    ),
)

_UNINSTALL_STATEMENTS: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;",
    "DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log;",
    "DROP FUNCTION IF EXISTS audit_log_block_mutation();",
)


def install_statements() -> tuple[str, ...]:
    """Idempotent DDL statements that install the immutability triggers.

    Each is a single command (asyncpg-safe). Callers execute them in order.
    """
    return _INSTALL_STATEMENTS


def uninstall_statements() -> tuple[str, ...]:
    """DDL statements that remove the immutability triggers + function."""
    return _UNINSTALL_STATEMENTS
