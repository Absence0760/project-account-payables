"""Seed the database with sample data for two demo tenants."""

import asyncio
import uuid
from datetime import date
from decimal import Decimal

import asyncpg
from passlib.context import CryptContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.database import control_engine, control_session_factory, _make_tenant_url
from app.models import Base
from app.models.organization import Organization
from app.models.user import User, Role, UserRole
from app.models.vendor import Vendor
from app.models.invoice import Invoice

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Fixed UUIDs for reproducibility
ACME_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ACME_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
TECH_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
TECH_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")

CONTROL_TABLES = {"organizations", "users", "roles", "user_roles"}


async def create_database(db_name: str) -> None:
    """Create a PostgreSQL database if it doesn't exist."""
    # Parse connection info from the async URL
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


async def create_control_tables():
    """Create control-plane tables (orgs, users, roles)."""
    async with control_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("  Control-plane tables ready")


async def create_tenant_tables(db_name: str):
    """Create tenant-scoped tables in a tenant database."""
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
        # Add columns that may be missing on existing tables
        for stmt in [
            "ALTER TABLE workflow_definitions ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE",
            "ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS steps_config_snapshot JSONB",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS warnings JSONB",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS vendor_address TEXT",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS vendor_tax_id VARCHAR(50)",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS ship_to_address TEXT",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tax_rate NUMERIC(5,2)",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS payment_method VARCHAR(50)",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS reference_number VARCHAR(100)",
        ]:
            await conn.execute(text(stmt))

    # ALTER TYPE ... ADD VALUE cannot run inside a transaction, so use a separate connection
    raw_url = tenant_url.replace("postgresql+asyncpg://", "")
    userpass, hostdb = raw_url.split("@", 1)
    pg_user, pg_pass = userpass.split(":", 1)
    host_port, db_name_parsed = hostdb.rsplit("/", 1)
    pg_host = host_port.split(":")[0]
    pg_port = int(host_port.split(":")[1]) if ":" in host_port else 5432
    try:
        conn = await asyncpg.connect(
            user=pg_user, password=pg_pass,
            host=pg_host, port=pg_port, database=db_name_parsed,
        )
        try:
            await conn.execute("ALTER TYPE invoicestatus ADD VALUE IF NOT EXISTS 'done'")
        except asyncpg.exceptions.DuplicateObjectError:
            pass
        finally:
            await conn.close()
    except Exception:
        pass  # enum may not exist yet on fresh DB (create_all already created it with 'done')
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
        admin_role = Role(name="admin", description="Full access to all features and user management")
        ap_manager_role = Role(name="ap_manager", description="Review and approve invoices")
        ap_clerk_role = Role(name="ap_clerk", description="Upload invoices and enter data")
        cfo_role = Role(name="cfo", description="Approve high-value invoices and view reports")
        session.add_all([admin_role, ap_manager_role, ap_clerk_role, cfo_role])

        # Acme users — one per role
        hashed = pwd_context.hash("demo")
        acme_admin = User(
            id=ACME_USER_ID, email="demo@acme.com", full_name="Alice Admin",
            hashed_password=hashed, organization_id=ACME_ORG_ID,
        )
        acme_manager = User(
            email="demo+apmanager@acme.com", full_name="Marcus Manager",
            hashed_password=hashed, organization_id=ACME_ORG_ID,
        )
        acme_clerk = User(
            email="demo+apclerk@acme.com", full_name="Clara Clerk",
            hashed_password=hashed, organization_id=ACME_ORG_ID,
        )
        acme_cfo = User(
            email="demo+cfo@acme.com", full_name="Frank CFO",
            hashed_password=hashed, organization_id=ACME_ORG_ID,
        )

        # TechFlow users
        tech_admin = User(
            id=TECH_USER_ID, email="admin@techflow.com", full_name="Tina TechAdmin",
            hashed_password=hashed, organization_id=TECH_ORG_ID,
        )
        tech_clerk = User(
            email="clerk@techflow.com", full_name="Carlos Clerk",
            hashed_password=hashed, organization_id=TECH_ORG_ID,
        )

        session.add_all([acme_admin, acme_manager, acme_clerk, acme_cfo, tech_admin, tech_clerk])
        await session.flush()

        # Assign roles
        session.add(UserRole(user_id=acme_admin.id, role_id=admin_role.id))
        session.add(UserRole(user_id=acme_manager.id, role_id=ap_manager_role.id))
        session.add(UserRole(user_id=acme_clerk.id, role_id=ap_clerk_role.id))
        session.add(UserRole(user_id=acme_cfo.id, role_id=cfo_role.id))
        session.add(UserRole(user_id=tech_admin.id, role_id=admin_role.id))
        session.add(UserRole(user_id=tech_clerk.id, role_id=ap_clerk_role.id))

        await session.commit()
        print("  Seeded 2 orgs, 6 users, 4 roles")


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
            Invoice(organization_id=org_id, invoice_number="INV-2024-001", vendor_name="Office Supplies Co", description="Monthly office supplies order", amount=Decimal("1250.00"), currency="USD", invoice_date=date(2024, 3, 15), received_date=date(2024, 3, 17), due_date=date(2024, 4, 15), payment_terms="Net 30", status="new", po_number="PO-2024-100", subtotal=Decimal("1100.00"), tax_amount=Decimal("110.00"), discount_amount=Decimal("0.00"), shipping_amount=Decimal("40.00"), bill_to_address="123 Main St, Suite 100, San Francisco, CA 94105", notes="Recurring monthly order", gl_account="6100", cost_center="ADMIN"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-002", vendor_name="Cloud Services Inc", description="Q1 cloud hosting fees", amount=Decimal("8500.00"), currency="USD", invoice_date=date(2024, 3, 31), received_date=date(2024, 4, 1), due_date=date(2024, 4, 20), payment_terms="Net 20", status="pending", po_number="PO-2024-101", subtotal=Decimal("8500.00"), tax_amount=Decimal("0.00"), bill_to_address="123 Main St, Suite 100, San Francisco, CA 94105", remit_to_address="PO Box 5000, Austin, TX 78701", gl_account="6200", cost_center="ENG"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-003", vendor_name="Facility Services Ltd", description="Building maintenance - March", amount=Decimal("3200.00"), currency="USD", invoice_date=date(2024, 3, 28), received_date=date(2024, 3, 29), due_date=date(2024, 4, 10), payment_terms="Net 10", status="ready_for_review", po_number="PO-2024-102", subtotal=Decimal("2900.00"), tax_amount=Decimal("300.00"), bill_to_address="123 Main St, Suite 100, San Francisco, CA 94105", gl_account="6300", cost_center="FACILITIES"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-004", vendor_name="Marketing Agency Pro", description="Social media campaign - February", amount=Decimal("5750.00"), currency="USD", invoice_date=date(2024, 3, 1), received_date=date(2024, 3, 3), due_date=date(2024, 5, 1), payment_terms="Net 60", status="sent_to_erp", po_number="PO-2024-103", subtotal=Decimal("5500.00"), tax_amount=Decimal("250.00"), approval_date=date(2024, 3, 15), approved_by="Jane Smith", gl_account="6400", cost_center="MARKETING"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-005", vendor_name="Tech Hardware Corp", description="Laptop procurement - 5 units", amount=Decimal("12000.00"), currency="USD", invoice_date=date(2024, 3, 20), received_date=date(2024, 3, 21), due_date=date(2024, 4, 25), payment_terms="Net 30", status="failed", po_number="PO-2024-104", subtotal=Decimal("11200.00"), tax_amount=Decimal("800.00"), shipping_amount=Decimal("0.00"), remit_to_address="500 Tech Blvd, San Jose, CA 95134", bill_to_address="123 Main St, Suite 100, San Francisco, CA 94105", gl_account="1500", cost_center="ENG"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-006", vendor_name="Legal Partners LLP", description="Legal consultation - Q1", amount=Decimal("4500.00"), currency="USD", invoice_date=date(2024, 4, 1), received_date=date(2024, 4, 2), due_date=date(2024, 5, 15), payment_terms="Net 45", status="new", po_number="PO-2024-105", subtotal=Decimal("4500.00"), tax_amount=Decimal("0.00"), remit_to_address="200 Legal Ave, New York, NY 10001", gl_account="6500", cost_center="LEGAL"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-007", vendor_name="Catering Solutions", description="Company event catering", amount=Decimal("2800.00"), currency="USD", invoice_date=date(2024, 3, 25), received_date=date(2024, 3, 26), due_date=date(2024, 4, 18), payment_terms="2/10 Net 30", status="pending", po_number="PO-2024-106", subtotal=Decimal("2600.00"), tax_amount=Decimal("200.00"), discount_amount=Decimal("52.00"), bill_to_address="123 Main St, Suite 100, San Francisco, CA 94105", notes="Early payment discount available", gl_account="6600", cost_center="HR"),
            Invoice(organization_id=org_id, invoice_number="INV-2024-008", vendor_name="Transport Logistics", description="Freight shipping - March", amount=Decimal("6300.00"), currency="USD", invoice_date=date(2024, 3, 30), received_date=date(2024, 4, 1), due_date=date(2024, 5, 5), payment_terms="Net 30", status="ready_for_review", po_number="PO-2024-107", subtotal=Decimal("5800.00"), tax_amount=Decimal("500.00"), shipping_amount=Decimal("0.00"), remit_to_address="800 Freight Way, Chicago, IL 60601", bill_to_address="123 Main St, Suite 100, San Francisco, CA 94105", gl_account="6700", cost_center="OPS"),
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
        await create_database(db_name)
        await create_tenant_tables(db_name)
        await seed_tenant(db_name, org_id, label)

    await control_engine.dispose()

    print("\nDone! Two tenants ready:")
    print("  acme.localhost:7777    — demo@acme.com / demo")
    print("  techflow.localhost:7777 — admin@techflow.com / demo")


if __name__ == "__main__":
    asyncio.run(seed())
