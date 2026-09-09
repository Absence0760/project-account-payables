"""Run Alembic migrations against all tenant databases."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

# Anchor THIS checkout's `backend/` on sys.path before `app` is imported below —
# same reason as `scripts/seed.py`, and the same two lines. Run as
# `python scripts/migrate_all_tenants.py`, sys.path[0] is `scripts/`, so `app`
# is not on sys.path and a worktree reusing the primary checkout's
# `backend/.venv` falls through to the editable install's baked-in finder and
# enumerates the OTHER checkout's tenants. (The `python -m alembic` child this
# spawns is separately covered by `alembic.ini`'s `prepend_sys_path`.)
#
# Inserted at 1 so `scripts/` keeps priority, matching seed.py. Idempotent, and
# a no-op in the checkout the venv was installed from. The path is spelled out
# twice rather than bound to a name because ruff's E402 exempts `sys.path`
# manipulation before imports but not an assignment beside it.
# See `frontend/tests-e2e/README.md` § Running from a worktree.
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(1, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import control_engine, control_session_factory
from app.models.organization import Organization


async def main():
    async with control_session_factory() as session:
        result = await session.execute(select(Organization.db_name))
        db_names = [row[0] for row in result.all()]

    await control_engine.dispose()

    if not db_names:
        print("No tenants found in control plane.")
        return

    print(f"Migrating {len(db_names)} tenant database(s)...")

    for db_name in db_names:
        print(f"\n--- {db_name} ---")
        env = {**os.environ, "FEOH_MIGRATE_TENANT": db_name}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  FAILED: {result.stderr}")
        else:
            print("  OK")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
