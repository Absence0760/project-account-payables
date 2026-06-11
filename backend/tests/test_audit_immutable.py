"""DB-level immutability of audit_log (SOX append-only control).

The app-layer "no PATCH/DELETE route" guard is in test_audit_append_only.py.
These tests prove the *durable* control: a pair of Postgres triggers that
reject any DELETE and any UPDATE touching a column other than `shipped_at`,
so even a rogue ORM call or a direct psql session can't tamper with the trail.

The `shipped_at` carve-out is required — the centralized audit-log shipper
legitimately stamps it. These tests assert both halves: edits are blocked, the
shipper's stamp still works.

Real-Postgres: `tenant_provisioning._create_tenant_tables` installs the
triggers on every freshly provisioned tenant (same DDL the migration runs).
Each test also calls `_ensure_triggers` (idempotent) so it's deterministic even
when the shared dev Postgres was last rebuilt by a concurrent worktree.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, InternalError

from app.models.workflow import AuditLog
from app.services.audit import log_action
from app.services.audit_immutability import install_statements


async def _ensure_triggers(mk) -> None:
    """Idempotently install the immutability triggers on the test tenant.

    Production tenants get them via migration 0022; the test harness provisions
    tenant tables via create_all. Because the dev Postgres is shared across
    concurrent worktrees, a tenant DB may have been (re)created by another
    process without the trigger — installing here (idempotent) makes the test
    deterministic regardless of which process built the DB. This is the real
    production DDL, not test scaffolding.
    """
    async with mk() as s:
        for stmt in install_statements():
            await s.execute(text(stmt))
        await s.commit()


async def _insert_row(mk, org_id) -> uuid.UUID:
    corr = uuid.uuid4()
    async with mk() as s:
        await log_action(
            s,
            correlation_id=corr,
            organization_id=org_id,
            actor_id=uuid.uuid4(),
            action="invoice.approved",
            entity_type="invoice",
            entity_id=uuid.uuid4(),
            details={"old_status": "ready_for_review", "new_status": "approved"},
        )
        await s.commit()
    return corr


@pytest.mark.asyncio
async def test_update_of_non_shipped_at_column_is_blocked(realdb):
    """Editing any column other than shipped_at must raise (trigger fires)."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _ensure_triggers(mk)
    corr = await _insert_row(mk, info.org_id)

    with pytest.raises((DBAPIError, InternalError)) as exc:
        async with mk() as s:
            await s.execute(
                update(AuditLog)
                .where(AuditLog.correlation_id == corr)
                .values(action="invoice.tampered")
            )
            await s.commit()
    assert "append-only" in str(exc.value)

    # The original value is intact.
    async with mk() as s:
        row = (
            await s.execute(select(AuditLog).where(AuditLog.correlation_id == corr))
        ).scalar_one()
    assert row.action == "invoice.approved"


@pytest.mark.asyncio
async def test_update_of_details_is_blocked(realdb):
    """Rewriting the change-history payload is the exact attack the trigger
    blocks — an auditor must trust `details` was never edited post-hoc."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _ensure_triggers(mk)
    corr = await _insert_row(mk, info.org_id)

    with pytest.raises((DBAPIError, InternalError)):
        async with mk() as s:
            await s.execute(
                update(AuditLog)
                .where(AuditLog.correlation_id == corr)
                .values(details={"old_status": "x", "new_status": "y"})
            )
            await s.commit()


@pytest.mark.asyncio
async def test_delete_of_audit_row_is_blocked(realdb):
    """DELETE must raise unconditionally — nothing may remove a trail row."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _ensure_triggers(mk)
    corr = await _insert_row(mk, info.org_id)

    with pytest.raises((DBAPIError, InternalError)) as exc:
        async with mk() as s:
            await s.execute(text("DELETE FROM audit_log WHERE correlation_id = :c"), {"c": corr})
            await s.commit()
    assert "append-only" in str(exc.value)

    async with mk() as s:
        count = (
            (await s.execute(select(AuditLog).where(AuditLog.correlation_id == corr)))
            .scalars()
            .all()
        )
    assert len(count) == 1  # still there


@pytest.mark.asyncio
async def test_shipped_at_only_update_succeeds(realdb):
    """The shipper's stamp is the one permitted mutation — without this
    carve-out the centralized WORM shipper could never mark rows shipped."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _ensure_triggers(mk)
    corr = await _insert_row(mk, info.org_id)

    async with mk() as s:
        await s.execute(
            text("UPDATE audit_log SET shipped_at = now() WHERE correlation_id = :c"),
            {"c": corr},
        )
        await s.commit()

    async with mk() as s:
        row = (
            await s.execute(select(AuditLog).where(AuditLog.correlation_id == corr))
        ).scalar_one()
    assert row.shipped_at is not None
    assert row.action == "invoice.approved"  # untouched


@pytest.mark.asyncio
async def test_shipped_at_plus_other_column_is_blocked(realdb):
    """Stamping shipped_at while ALSO editing another column must still be
    blocked — otherwise the carve-out becomes a tamper bypass."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _ensure_triggers(mk)
    corr = await _insert_row(mk, info.org_id)

    with pytest.raises((DBAPIError, InternalError)):
        async with mk() as s:
            await s.execute(
                text(
                    "UPDATE audit_log SET shipped_at = now(), action = 'x' "
                    "WHERE correlation_id = :c"
                ),
                {"c": corr},
            )
            await s.commit()


@pytest.mark.asyncio
async def test_install_statements_are_idempotent(realdb):
    """Re-installing the triggers is a no-op (migration may run twice across a
    rebuild / merge revision). Applying again must not error or duplicate."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _ensure_triggers(mk)
    async with mk() as s:
        for stmt in install_statements():
            await s.execute(text(stmt))
        await s.commit()

    # Still enforcing after a re-install.
    corr = await _insert_row(mk, info.org_id)
    with pytest.raises((DBAPIError, InternalError)):
        async with mk() as s:
            await s.execute(
                update(AuditLog).where(AuditLog.correlation_id == corr).values(action="z")
            )
            await s.commit()
