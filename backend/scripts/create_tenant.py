"""Provision a new tenant: create org, admin user, database, and tables."""

import argparse
import asyncio
import uuid

import asyncpg
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.database import control_engine, control_session_factory, _make_tenant_url
from app.models import Base
from app.models.organization import Organization
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Tables that belong in tenant DBs (exclude control-plane tables)
CONTROL_TABLES = {"organizations", "users", "roles", "user_roles"}


async def create_database(db_name: str) -> None:
    """Create a new PostgreSQL database using asyncpg."""
    url = settings.database_url.replace("postgresql+asyncpg://", "")
    userpass, hostdb = url.split("@", 1)
    user, password = userpass.split(":", 1)
    host_port, _ = hostdb.rsplit("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":", 1)
        port = int(port)
    else:
        host, port = host_port, 5432

    conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database="postgres")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            print(f"  Created database: {db_name}")
        else:
            print(f"  Database already exists: {db_name}")
    finally:
        await conn.close()


async def create_tenant_tables(db_name: str) -> None:
    """Create tenant-scoped tables in the tenant database."""
    tenant_url = _make_tenant_url(db_name)
    engine = create_async_engine(tenant_url)
    tenant_tables = [
        table for name, table in Base.metadata.tables.items()
        if name not in CONTROL_TABLES
    ]
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tenant_tables, checkfirst=True)
        )
    await engine.dispose()
    print(f"  Created tenant tables in: {db_name}")


async def main():
    parser = argparse.ArgumentParser(description="Provision a new tenant")
    parser.add_argument("--name", required=True, help="Organization name")
    parser.add_argument("--slug", required=True, help="URL slug (e.g., 'acme')")
    parser.add_argument("--plan", default="pro", help="Plan tier (default: pro)")
    parser.add_argument("--admin-email", required=True, help="Admin user email")
    parser.add_argument("--admin-password", required=True, help="Admin user password")
    args = parser.parse_args()

    db_name = f"{settings.tenant_db_prefix}{args.slug}"

    print(f"Provisioning tenant: {args.name} (slug={args.slug}, db={db_name})")

    # 1. Create the PostgreSQL database
    await create_database(db_name)

    # 2. Insert org + user into control-plane DB
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with control_session_factory() as session:
        org = Organization(
            id=org_id,
            name=args.name,
            slug=args.slug,
            plan=args.plan,
            db_name=db_name,
        )
        user = User(
            id=user_id,
            email=args.admin_email,
            full_name=f"{args.name} Admin",
            hashed_password=pwd_context.hash(args.admin_password),
            is_active=True,
            organization_id=org_id,
        )
        session.add(org)
        session.add(user)
        await session.commit()
        print(f"  Created org ({org_id}) and user ({args.admin_email}) in control plane")

    # 3. Create tenant tables
    await create_tenant_tables(db_name)

    print(f"\nTenant '{args.slug}' is ready!")
    print(f"  Login at: {args.slug}.localhost:7777")
    print(f"  Credentials: {args.admin_email} / {args.admin_password}")

    await control_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
