"""Regression tests for ``scripts/rename_databases_to_feohledger.py``.

The script is the DB half of the FeohLedger rename runbook
(``docs/feohledger-rename-migration.md`` §2): rename the control-plane and
tenant databases AND rewrite ``organizations.db_name`` so the two can't drift.

These tests drive the real ``main()`` against scratch databases on the local
Postgres (skipped when unreachable, mirroring the realdb harness) because the
bug class the script explicitly guards against — SQL LIKE treating ``_`` as a
single-character wildcard, so ``ap_%`` also matches an unrelated ``apiserver``
— lives in SQL/matching semantics a mocked connection cannot observe.

Every name is uniquified per run so concurrent pytest processes can't collide,
and none carry the real ``ap_``/``feoh_`` prefixes, so a stray dev database
can never be caught by the sweep.
"""

from __future__ import annotations

import sys
import uuid

import asyncpg
import pytest

from app.services.tenant_provisioning import _parse_maintenance_dsn
from scripts.rename_databases_to_feohledger import _rename_database, main


async def _connect_maintenance() -> asyncpg.Connection:
    dsn = _parse_maintenance_dsn()
    try:
        return await asyncpg.connect(**dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"requires a live Postgres (pnpm db:up): {exc}")


async def _db_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name))


async def _drop_dbs(conn: asyncpg.Connection, *names: str) -> None:
    for name in names:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


async def _create_control_db(
    maint: asyncpg.Connection, dsn: dict, name: str, db_name_rows: list[str]
) -> None:
    """Create a scratch control DB holding a minimal ``organizations`` table."""
    await maint.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(**{**dsn, "database": name})
    try:
        await conn.execute("CREATE TABLE organizations (db_name text NOT NULL)")
        for row in db_name_rows:
            await conn.execute("INSERT INTO organizations (db_name) VALUES ($1)", row)
    finally:
        await conn.close()


async def _org_db_names(dsn: dict, control_db: str) -> set[str]:
    conn = await asyncpg.connect(**{**dsn, "database": control_db})
    try:
        return {r["db_name"] for r in await conn.fetch("SELECT db_name FROM organizations")}
    finally:
        await conn.close()


async def _run_script(*argv: str) -> int:
    """Invoke the script's real ``main()`` with a substituted argv."""
    old_argv = sys.argv
    sys.argv = ["rename_databases_to_feohledger.py", *argv]
    try:
        return await main()
    finally:
        sys.argv = old_argv


class _Names:
    """Per-run unique database names; none share the real ap_/feoh_ prefixes."""

    def __init__(self):
        sfx = uuid.uuid4().hex[:8]
        self.old_prefix = f"rnt{sfx}_"
        self.new_prefix = f"fnt{sfx}_"
        self.tenant_old = f"rnt{sfx}_acme"
        self.tenant_new = f"fnt{sfx}_acme"
        # The LIKE-wildcard trap: under LIKE, 'rnt<sfx>_%' matches this name
        # (the `_` consumes the `x`); under startswith/left() it must not.
        self.trap = f"rnt{sfx}xacme"
        # A slug that itself contains the old prefix — only the LEADING prefix
        # may be swapped, the rest of the name stays verbatim.
        self.nested_row = f"rnt{sfx}_rnt{sfx}_x"
        self.nested_row_renamed = f"fnt{sfx}_rnt{sfx}_x"
        self.ctl_old = f"rnt{sfx}ctl"
        self.ctl_new = f"rnt{sfx}newctl"

    def script_args(self) -> list[str]:
        return [
            "--old-control-db",
            self.ctl_old,
            "--new-control-db",
            self.ctl_new,
            "--old-tenant-prefix",
            self.old_prefix,
            "--new-tenant-prefix",
            self.new_prefix,
        ]


async def test_apply_renames_tenants_control_and_rows_but_not_lookalikes():
    maint = await _connect_maintenance()
    dsn = _parse_maintenance_dsn()
    n = _Names()
    try:
        await maint.execute(f'CREATE DATABASE "{n.tenant_old}"')
        await maint.execute(f'CREATE DATABASE "{n.trap}"')
        await _create_control_db(maint, dsn, n.ctl_old, [n.tenant_old, n.trap, n.nested_row])

        assert await _run_script("--apply", *n.script_args()) == 0

        # Tenant DB renamed; the LIKE-wildcard lookalike untouched.
        assert await _db_exists(maint, n.tenant_new)
        assert not await _db_exists(maint, n.tenant_old)
        assert await _db_exists(maint, n.trap)
        # Control plane renamed.
        assert await _db_exists(maint, n.ctl_new)
        assert not await _db_exists(maint, n.ctl_old)
        # organizations.db_name rewritten in lockstep: prefixed rows swap only
        # their LEADING prefix, the lookalike row stays verbatim.
        assert await _org_db_names(dsn, n.ctl_new) == {
            n.tenant_new,
            n.trap,
            n.nested_row_renamed,
        }

        # Idempotent: a re-run after completion is all skips, still exit 0.
        assert await _run_script("--apply", *n.script_args()) == 0
        assert await _db_exists(maint, n.tenant_new)
        assert await _db_exists(maint, n.trap)
    finally:
        await _drop_dbs(maint, n.tenant_old, n.tenant_new, n.trap, n.ctl_old, n.ctl_new)
        await maint.close()


async def test_dry_run_changes_nothing():
    maint = await _connect_maintenance()
    dsn = _parse_maintenance_dsn()
    n = _Names()
    try:
        await maint.execute(f'CREATE DATABASE "{n.tenant_old}"')
        await _create_control_db(maint, dsn, n.ctl_old, [n.tenant_old])

        assert await _run_script(*n.script_args()) == 0  # no --apply

        assert await _db_exists(maint, n.tenant_old)
        assert not await _db_exists(maint, n.tenant_new)
        assert await _db_exists(maint, n.ctl_old)
        assert not await _db_exists(maint, n.ctl_new)
        assert await _org_db_names(dsn, n.ctl_old) == {n.tenant_old}
    finally:
        await _drop_dbs(maint, n.tenant_old, n.tenant_new, n.ctl_old, n.ctl_new)
        await maint.close()


async def test_conflict_when_both_names_exist_exits_nonzero_and_renames_nothing():
    maint = await _connect_maintenance()
    dsn = _parse_maintenance_dsn()
    n = _Names()
    try:
        await maint.execute(f'CREATE DATABASE "{n.tenant_old}"')
        await maint.execute(f'CREATE DATABASE "{n.tenant_new}"')
        await _create_control_db(maint, dsn, n.ctl_old, [])

        assert await _run_script("--apply", *n.script_args()) == 1

        # Neither side of the conflicted pair was touched.
        assert await _db_exists(maint, n.tenant_old)
        assert await _db_exists(maint, n.tenant_new)
    finally:
        await _drop_dbs(maint, n.tenant_old, n.tenant_new, n.ctl_old, n.ctl_new)
        await maint.close()


async def test_unsafe_db_name_rejected_before_any_ddl():
    # The guard fires before the connection is used at all, so no DB needed.
    with pytest.raises(ValueError, match="unsafe"):
        await _rename_database(None, 'bad";drop', "fine_name", apply=True, force=False)
    with pytest.raises(ValueError, match="unsafe"):
        await _rename_database(None, "fine_name", "Bad-Upper", apply=True, force=False)
