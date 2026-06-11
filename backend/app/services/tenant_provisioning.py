"""Tenant provisioning — create org, admin user, DB, and tables.

Shared by scripts/create_tenant.py (CLI) and the self-service signup
endpoint (app/api/signup.py). Splitting the work into explicit phases lets
the signup flow run each step inside the request lifecycle and surface
partial-failure diagnostics without re-running everything on retry.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import asyncpg
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models import Base
from app.models.organization import Organization
from app.models.user import Role, User, UserRole
from app.utils.passwords import pwd_context

logger = logging.getLogger(__name__)

# Tables that live in the CONTROL plane DB and must NOT be created inside a
# tenant DB. Anything not in this set belongs to the tenant schema.
CONTROL_TABLES: frozenset[str] = frozenset(
    {"organizations", "users", "roles", "user_roles", "email_verifications"}
)


@dataclass
class ProvisioningResult:
    organization_id: uuid.UUID
    user_id: uuid.UUID
    db_name: str


def _parse_maintenance_dsn() -> dict:
    """Parse host/port/user/password out of the configured async URL, for an
    asyncpg connection to the 'postgres' maintenance DB."""
    url = settings.database_url.replace("postgresql+asyncpg://", "")
    userpass, hostdb = url.split("@", 1)
    user, password = userpass.split(":", 1)
    host_port, _ = hostdb.rsplit("/", 1)
    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
    else:
        host, port = host_port, 5432
    return {"host": host, "port": port, "user": user, "password": password, "database": "postgres"}


async def _create_postgres_database(db_name: str) -> bool:
    """Create a new Postgres database (CREATE DATABASE cannot be in a transaction).

    Returns True if this call created the database, False if it already existed
    — so the caller knows whether it's safe to drop on a later failure.
    """
    conn = await asyncpg.connect(**_parse_maintenance_dsn())
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if exists:
            logger.info("Database already exists: %s", db_name)
            return False
        await conn.execute(f'CREATE DATABASE "{db_name}"')
        logger.info("Created database: %s", db_name)
        return True
    finally:
        await conn.close()


async def _drop_postgres_database(db_name: str) -> None:
    """Drop a tenant DB we created during a provisioning attempt that then
    failed, so a partial failure doesn't leak orphan databases."""
    conn = await asyncpg.connect(**_parse_maintenance_dsn())
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        logger.info("Dropped orphan database after failed provisioning: %s", db_name)
    finally:
        await conn.close()


async def _create_tenant_tables(db_name: str) -> None:
    tenant_url = _make_tenant_url(db_name)
    engine = create_async_engine(tenant_url)
    tenant_tables = [
        table for name, table in Base.metadata.tables.items() if name not in CONTROL_TABLES
    ]
    async with engine.begin() as conn:
        # pgvector extension is required by the RAG embeddings table
        # (app.models.invoice_embedding). CREATE EXTENSION must run before
        # create_all, because the Vector column type resolves at DDL time.
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=tenant_tables, checkfirst=True
            )
        )
    await engine.dispose()
    logger.info("Created tenant tables in: %s", db_name)


async def provision_tenant(
    *,
    company_name: str,
    slug: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    plan: str = "free",
    must_change_password: bool = True,
) -> ProvisioningResult:
    """End-to-end: DB + org + admin user + tenant tables.

    On success, the admin can log in immediately at the tenant subdomain
    and will be forced to change their password on first login (unless
    must_change_password is disabled for internal tenants).
    """
    db_name = f"{settings.tenant_db_prefix}{slug}"

    created_db = await _create_postgres_database(db_name)
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    try:
        return await _provision_into(
            db_name=db_name,
            org_id=org_id,
            user_id=user_id,
            company_name=company_name,
            slug=slug,
            admin_email=admin_email,
            admin_name=admin_name,
            admin_password=admin_password,
            plan=plan,
            must_change_password=must_change_password,
        )
    except Exception:
        # The control-plane insert or tenant-table creation failed after the DB
        # was created — drop the orphan so retries (and the namespace) stay clean.
        # Only drop a DB this call created; never a pre-existing one.
        if created_db:
            try:
                await _drop_postgres_database(db_name)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to drop orphan database after provisioning error")
        raise


async def _provision_into(
    *,
    db_name: str,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    company_name: str,
    slug: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    plan: str,
    must_change_password: bool,
) -> ProvisioningResult:
    async with control_session_factory() as session:
        org = Organization(
            id=org_id,
            name=company_name,
            slug=slug,
            plan=plan,
            db_name=db_name,
        )
        user = User(
            id=user_id,
            email=admin_email,
            full_name=admin_name,
            hashed_password=pwd_context.hash(admin_password),
            is_active=True,
            organization_id=org_id,
            must_change_password=must_change_password,
        )

        # Grant the admin role if it exists (created by seed.py). In a fresh
        # DB without seeds, skip silently — RBAC roles can be provisioned
        # later by an admin endpoint.
        admin_role = (
            await session.execute(select(Role).where(Role.name == "admin"))
        ).scalar_one_or_none()

        session.add(org)
        session.add(user)
        await session.flush()

        if admin_role is not None:
            session.add(UserRole(user_id=user_id, role_id=admin_role.id))

        await session.commit()
        logger.info("Provisioned org %s (%s) + admin user %s", slug, org_id, admin_email)

    await _create_tenant_tables(db_name)

    return ProvisioningResult(
        organization_id=org_id,
        user_id=user_id,
        db_name=db_name,
    )
