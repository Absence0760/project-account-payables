"""Seed the database with sample data for two demo tenants."""

import asyncio
import uuid
from datetime import date
from decimal import Decimal

from passlib.context import CryptContext
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.database import control_engine, control_session_factory, _make_tenant_url
from app.models import Base
from app.models.organization import Organization
from app.models.user import User, Role
from app.models.vendor import Vendor
from app.models.invoice import Invoice

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Fixed UUIDs for reproducibility
ACME_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ACME_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
TECH_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
TECH_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")

CONTROL_TABLES = {"organizations", "users", "roles", "user_roles"}


def create_database(db_name: str) -> None:
    """Create a PostgreSQL database if it doesn't exist."""
    sync_url = settings.database_url.replace("+asyncpg", "").rsplit("/", 1)[0] + "/postgres"
    engine = create_engine(sync_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        )
        if not result.scalar():
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            print(f"  Created database: {db_name}")
        else:
            print(f"  Database already exists: {db_name}")
    engine.dispose()


async def create_control_tables():
    """Create control-plane tables (orgs, users, roles)."""
    async with control_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("  Control-plane tables ready")


async def create_tenant_tables(db_name: str):
    """Create tenant-scoped tables in a tenant database."""
    tenant_url = _make_tenant_url(db_name)
    engine = create_async_engine(tenant_url)
    async with engine.begin() as conn:
        tenant_tables = [
            table for name, table in Base.metadata.tables.items()
            if name not in CONTROL_TABLES
        ]
        for table in tenant_tables:
            await conn.run_sync(table.create, checkfirst=True)
    await engine.dispose()
    print(f"  Tenant tables ready in: {db_name}")


async def seed_control_plane():
    """Seed organizations, users, and roles into the control-plane DB."""
    async with control_session_factory() as session:
        # Check if already seeded
        result = await session.execute(
            text("SELECT count(*) FROM organizations WHERE id = :id"),
            {"id": ACME_ORG_ID},
        )
        if result.scalar() > 0:
            print("  Control plane already seeded. Skipping.")
            return

        # Orgs
        session.add(Organization(
            id=ACME_ORG_ID, name="Acme Corp", slug="acme",
            plan="pro", db_name="ap_acme",
        ))
        session.add(Organization(
            id=TECH_ORG_ID, name="TechFlow Inc", slug="techflow",
            plan="pro", db_name="ap_techflow",
        ))

        # Roles
        for role_name in ("admin", "ap_manager", "ap_clerk", "cfo"):
            session.add(Role(name=role_name, description=f"{role_name} role"))

        # Users
        session.add(User(
            id=ACME_USER_ID, email="demo@acme.com", full_name="Acme Demo User",
            hashed_password=pwd_context.hash("demo"), organization_id=ACME_ORG_ID,
        ))
        session.add(User(
            id=TECH_USER_ID, email="admin@techflow.com", full_name="TechFlow Admin",
            hashed_password=pwd_context.hash("demo"), organization_id=TECH_ORG_ID,
        ))

        await session.commit()
        print("  Seeded 2 orgs, 2 users, 4 roles")


async def seed_tenant(db_name: str, org_id: uuid.UUID, tenant_label: str):
    """Seed vendors and invoices into a tenant database."""
    tenant_url = _make_tenant_url(db_name)
    engine = create_async_engine(tenant_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        # Check if already seeded
        result = await session.execute(text("SELECT count(*) FROM vendors"))
        if result.scalar() > 0:
            print(f"  {tenant_label} tenant already seeded. Skipping.")
            await engine.dispose()
            return

        vendors = [
            Vendor(organization_id=org_id, name="Office Supplies Co", code="OSC"),
            Vendor(organization_id=org_id, name="Cloud Services Inc", code="CSI"),
            Vendor(organization_id=org_id, name="Facility Services Ltd", code="FSL"),
            Vendor(organization_id=org_id, name="Marketing Agency Pro", code="MAP"),
            Vendor(organization_id=org_id, name="Tech Hardware Corp", code="THC"),
            Vendor(organization_id=org_id, name="Legal Partners LLP", code="LPL"),
            Vendor(organization_id=org_id, name="Catering Solutions", code="CS"),
            Vendor(organization_id=org_id, name="Transport Logistics", code="TL"),
        ]
        session.add_all(vendors)

        invoices = [
            Invoice(organization_id=org_id, invoice_number="INV-2024-001", vendor_name="Office Supplies Co", description="Monthly office supplies order", amount=Decimal("1250.00"), currency="USD", due_date=date(2024, 4, 15), status="new", po_number="PO-2024-100"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-002", vendor_name="Cloud Services Inc", description="Q1 cloud hosting fees", amount=Decimal("8500.00"), currency="USD", due_date=date(2024, 4, 20), status="pending", po_number="PO-2024-101"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-003", vendor_name="Facility Services Ltd", description="Building maintenance - March", amount=Decimal("3200.00"), currency="USD", due_date=date(2024, 4, 10), status="ready_for_review", po_number="PO-2024-102"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-004", vendor_name="Marketing Agency Pro", description="Social media campaign - February", amount=Decimal("5750.00"), currency="USD", due_date=date(2024, 5, 1), status="sent_to_erp", po_number="PO-2024-103"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-005", vendor_name="Tech Hardware Corp", description="Laptop procurement - 5 units", amount=Decimal("12000.00"), currency="USD", due_date=date(2024, 4, 25), status="failed", po_number="PO-2024-104"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-006", vendor_name="Legal Partners LLP", description="Legal consultation - Q1", amount=Decimal("4500.00"), currency="USD", due_date=date(2024, 5, 15), status="new", po_number="PO-2024-105"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-007", vendor_name="Catering Solutions", description="Company event catering", amount=Decimal("2800.00"), currency="USD", due_date=date(2024, 4, 18), status="pending", po_number="PO-2024-106"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-008", vendor_name="Transport Logistics", description="Freight shipping - March", amount=Decimal("6300.00"), currency="USD", due_date=date(2024, 5, 5), status="ready_for_review", po_number="PO-2024-107"),
        ]
        session.add_all(invoices)

        await session.commit()
        print(f"  Seeded {tenant_label}: {len(vendors)} vendors, {len(invoices)} invoices")

    await engine.dispose()


async def seed():
    print("=== Seeding control plane ===")
    await create_control_tables()
    await seed_control_plane()

    for db_name, org_id, label in [
        ("ap_acme", ACME_ORG_ID, "Acme Corp"),
        ("ap_techflow", TECH_ORG_ID, "TechFlow Inc"),
    ]:
        print(f"\n=== Seeding tenant: {label} ({db_name}) ===")
        create_database(db_name)
        await create_tenant_tables(db_name)
        await seed_tenant(db_name, org_id, label)

    await control_engine.dispose()

    print("\nDone! Two tenants ready:")
    print("  acme.localhost:7777    — demo@acme.com / demo")
    print("  techflow.localhost:7777 — admin@techflow.com / demo")


if __name__ == "__main__":
    asyncio.run(seed())
