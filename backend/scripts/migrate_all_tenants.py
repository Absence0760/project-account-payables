"""Run Alembic migrations against all tenant databases."""

import asyncio
import os
import subprocess
import sys

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
        env = {**os.environ, "AP_MIGRATE_TENANT": db_name}
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
