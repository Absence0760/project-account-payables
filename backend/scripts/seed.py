"""Seed the database with sample data for two demo tenants."""

import asyncio
import uuid
from datetime import UTC, date
from decimal import Decimal

import asyncpg
from passlib.context import CryptContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_engine, control_session_factory
from app.models import Base
from app.models.exception import Exception as APException
from app.models.gl_account import GLAccount
from app.models.invoice import Invoice, InvoiceExtractionResult, InvoiceLineItem
from app.models.invoice_embedding import InvoiceEmbedding
from app.models.organization import Organization
from app.models.payment import Payment, PaymentRun, PaymentSchedule
from app.models.procurement import GoodsReceipt, GRLineItem, POLineItem, PurchaseOrder
from app.models.usage import ExtractionUsage
from app.models.user import Role, User, UserRole
from app.models.vendor import Vendor

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Fixed UUIDs for reproducibility
ACME_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ACME_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
TECH_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
TECH_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")

CONTROL_TABLES = {
    "organizations",
    "users",
    "roles",
    "user_roles",
    "email_verifications",
}


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

    conn = await asyncpg.connect(
        host=host, port=port, user=user, password=password, database="postgres"
    )
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
    """Create control-plane tables (orgs, users, roles, email_verifications).

    Filters Base.metadata to the control set so we don't try to create
    tenant-scoped tables (like invoice_embeddings, which needs pgvector)
    in the control DB.
    """
    control_tables = [
        table for name, table in Base.metadata.tables.items() if name in CONTROL_TABLES
    ]
    async with control_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=control_tables, checkfirst=True
            )
        )
    print("  Control-plane tables ready")


async def create_tenant_tables(db_name: str):
    """Create tenant-scoped tables in a tenant database."""
    tenant_url = _make_tenant_url(db_name)
    engine = create_async_engine(tenant_url)
    tenant_tables = [
        table for name, table in Base.metadata.tables.items() if name not in CONTROL_TABLES
    ]
    async with engine.begin() as conn:
        # pgvector extension — required by invoice_embeddings (RAG priors).
        # Must run before create_all so the Vector column type resolves.
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=tenant_tables, checkfirst=True
            )
        )
        # Add columns that may be missing on existing tables
        for stmt in [
            "ALTER TABLE workflow_definitions "
            "ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE",
            "ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS steps_config_snapshot JSONB",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS warnings JSONB",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS vendor_address TEXT",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS vendor_tax_id VARCHAR(50)",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS ship_to_address TEXT",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tax_rate NUMERIC(5,2)",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS payment_method VARCHAR(50)",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS reference_number VARCHAR(100)",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS rejected_by VARCHAR(255)",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS assigned_to_id UUID",
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(255)",
            "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS tax_id VARCHAR(50)",
            "ALTER TABLE vendors "
            "ADD COLUMN IF NOT EXISTS accepts_virtual_cards "
            "BOOLEAN DEFAULT FALSE",
            "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS status VARCHAR(30) DEFAULT 'active'",
            "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS source VARCHAR(30) DEFAULT 'manual'",
            "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS verified_by VARCHAR(255)",
            "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ",
            "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS erp_vendor_id VARCHAR(255)",
            "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS erp_synced_at TIMESTAMPTZ",
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
            user=pg_user,
            password=pg_pass,
            host=pg_host,
            port=pg_port,
            database=db_name_parsed,
        )
        try:
            for val in ("done", "posted_in_erp", "payment_scheduled", "paid"):
                await conn.execute(f"ALTER TYPE invoicestatus ADD VALUE IF NOT EXISTS '{val}'")
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
        session.add(
            Organization(
                id=ACME_ORG_ID,
                name="Acme Corp",
                slug="acme",
                plan="pro",
                db_name="ap_acme",
            )
        )
        session.add(
            Organization(
                id=TECH_ORG_ID,
                name="TechFlow Inc",
                slug="techflow",
                plan="pro",
                db_name="ap_techflow",
            )
        )

        # Roles
        admin_role = Role(
            name="admin", description="Full access to all features and user management"
        )
        ap_manager_role = Role(name="ap_manager", description="Review and approve invoices")
        ap_clerk_role = Role(name="ap_clerk", description="Upload invoices and enter data")
        cfo_role = Role(name="cfo", description="Approve high-value invoices and view reports")
        session.add_all([admin_role, ap_manager_role, ap_clerk_role, cfo_role])

        # Acme users — one per role
        hashed = pwd_context.hash("demo")
        acme_admin = User(
            id=ACME_USER_ID,
            email="demo@acme.com",
            full_name="Alice Admin",
            hashed_password=hashed,
            organization_id=ACME_ORG_ID,
        )
        acme_manager = User(
            email="demo+apmanager@acme.com",
            full_name="Marcus Manager",
            hashed_password=hashed,
            organization_id=ACME_ORG_ID,
        )
        acme_clerk = User(
            email="demo+apclerk@acme.com",
            full_name="Clara Clerk",
            hashed_password=hashed,
            organization_id=ACME_ORG_ID,
        )
        acme_cfo = User(
            email="demo+cfo@acme.com",
            full_name="Frank CFO",
            hashed_password=hashed,
            organization_id=ACME_ORG_ID,
        )

        # TechFlow users
        tech_admin = User(
            id=TECH_USER_ID,
            email="admin@techflow.com",
            full_name="Tina TechAdmin",
            hashed_password=hashed,
            organization_id=TECH_ORG_ID,
        )
        tech_clerk = User(
            email="clerk@techflow.com",
            full_name="Carlos Clerk",
            hashed_password=hashed,
            organization_id=TECH_ORG_ID,
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

        # Vendors — mix of sources and statuses
        v_office = Vendor(
            organization_id=org_id,
            name="Office Supplies Co",
            code="OSC",
            email="ap@officesupplies.com",
            address="100 Supply Ave, Dallas, TX 75201",
            tax_id="12-3456789",
            payment_terms="Net 30",
            status="active",
            source="erp_sync",
            erp_vendor_id="ERP-V001",
            accepts_virtual_cards=True,
        )
        v_cloud = Vendor(
            organization_id=org_id,
            name="Cloud Services Inc",
            code="CSI",
            email="billing@cloudservices.com",
            address="PO Box 5000, Austin, TX 78701",
            tax_id="98-7654321",
            payment_terms="Net 20",
            status="active",
            source="erp_sync",
            erp_vendor_id="ERP-V002",
        )
        v_facility = Vendor(
            organization_id=org_id,
            name="Facility Services Ltd",
            code="FSL",
            email="invoices@facilityservices.com",
            address="250 Maintenance Blvd, Denver, CO 80202",
            payment_terms="Net 10",
            status="active",
            source="manual",
        )
        v_marketing = Vendor(
            organization_id=org_id,
            name="Marketing Agency Pro",
            code="MAP",
            email="finance@marketingpro.com",
            address="500 Creative Way, Los Angeles, CA 90001",
            payment_terms="Net 60",
            status="active",
            source="manual",
            accepts_virtual_cards=True,
        )
        v_tech = Vendor(
            organization_id=org_id,
            name="Tech Hardware Corp",
            code="THC",
            email="ar@techhardware.com",
            address="500 Tech Blvd, San Jose, CA 95134",
            tax_id="55-1234567",
            payment_terms="Net 30",
            status="active",
            source="erp_sync",
            erp_vendor_id="ERP-V005",
        )
        v_legal = Vendor(
            organization_id=org_id,
            name="Legal Partners LLP",
            code="LPL",
            email="billing@legalpartners.com",
            address="200 Legal Ave, New York, NY 10001",
            payment_terms="Net 45",
            status="active",
            source="manual",
        )
        v_catering = Vendor(
            organization_id=org_id,
            name="Catering Solutions",
            code="CS",
            email="orders@cateringsolutions.com",
            payment_terms="2/10 Net 30",
            status="active",
            source="manual",
            accepts_virtual_cards=True,
        )
        v_transport = Vendor(
            organization_id=org_id,
            name="Transport Logistics",
            code="TL",
            email="ap@transportlogistics.com",
            address="800 Freight Way, Chicago, IL 60601",
            payment_terms="Net 30",
            status="active",
            source="erp_sync",
            erp_vendor_id="ERP-V008",
        )
        # Unverified vendor (AI-extracted from a recent invoice)
        v_unverified = Vendor(
            organization_id=org_id,
            name="Global Printing Services",
            code=None,
            email=None,
            address="42 Print Lane, Portland, OR 97201",
            status="unverified",
            source="ai_extracted",
        )
        # Rejected duplicate
        v_rejected = Vendor(
            organization_id=org_id,
            name="Office Supplies Company",
            code=None,
            email=None,
            status="rejected",
            source="ai_extracted",
        )

        all_vendors = [
            v_office,
            v_cloud,
            v_facility,
            v_marketing,
            v_tech,
            v_legal,
            v_catering,
            v_transport,
            v_unverified,
            v_rejected,
        ]
        session.add_all(all_vendors)
        await session.flush()

        # Invoices — linked to vendor records via vendor_id
        bill_to = "123 Main St, Suite 100, San Francisco, CA 94105"
        invoices = [
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-001",
                vendor_name="Office Supplies Co",
                vendor_id=v_office.id,
                description="Monthly office supplies order",
                amount=Decimal("1250.00"),
                currency="USD",
                invoice_date=date(2024, 3, 15),
                received_date=date(2024, 3, 17),
                due_date=date(2024, 4, 15),
                payment_terms="Net 30",
                status="new",
                po_number="PO-2024-100",
                subtotal=Decimal("1100.00"),
                tax_amount=Decimal("110.00"),
                discount_amount=Decimal("0.00"),
                shipping_amount=Decimal("40.00"),
                bill_to_address=bill_to,
                vendor_address="100 Supply Ave, Dallas, TX 75201",
                vendor_tax_id="12-3456789",
                notes="Recurring monthly order",
                gl_account="6100",
                cost_center="ADMIN",
            ),
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-002",
                vendor_name="Cloud Services Inc",
                vendor_id=v_cloud.id,
                description="Q1 cloud hosting fees",
                amount=Decimal("8500.00"),
                currency="USD",
                invoice_date=date(2024, 3, 31),
                received_date=date(2024, 4, 1),
                due_date=date(2024, 4, 20),
                payment_terms="Net 20",
                status="pending",
                po_number="PO-2024-101",
                subtotal=Decimal("8500.00"),
                tax_amount=Decimal("0.00"),
                bill_to_address=bill_to,
                remit_to_address="PO Box 5000, Austin, TX 78701",
                gl_account="6200",
                cost_center="ENG",
            ),
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-003",
                vendor_name="Facility Services Ltd",
                vendor_id=v_facility.id,
                description="Building maintenance - March",
                amount=Decimal("3200.00"),
                currency="USD",
                invoice_date=date(2024, 3, 28),
                received_date=date(2024, 3, 29),
                due_date=date(2024, 4, 10),
                payment_terms="Net 10",
                status="ready_for_review",
                po_number="PO-2024-102",
                subtotal=Decimal("2900.00"),
                tax_amount=Decimal("300.00"),
                bill_to_address=bill_to,
                gl_account="6300",
                cost_center="FACILITIES",
            ),
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-004",
                vendor_name="Marketing Agency Pro",
                vendor_id=v_marketing.id,
                description="Social media campaign - February",
                amount=Decimal("5750.00"),
                currency="USD",
                invoice_date=date(2024, 3, 1),
                received_date=date(2024, 3, 3),
                due_date=date(2024, 5, 1),
                payment_terms="Net 60",
                status="approved",
                po_number="PO-2024-103",
                subtotal=Decimal("5500.00"),
                tax_amount=Decimal("250.00"),
                approval_date=date(2024, 3, 15),
                approved_by="Marcus Manager",
                gl_account="6400",
                cost_center="MARKETING",
            ),
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-005",
                vendor_name="Tech Hardware Corp",
                vendor_id=v_tech.id,
                description="Laptop procurement - 5 units",
                amount=Decimal("12000.00"),
                currency="USD",
                invoice_date=date(2024, 3, 20),
                received_date=date(2024, 3, 21),
                due_date=date(2024, 4, 25),
                payment_terms="Net 30",
                status="posted_in_erp",
                po_number="PO-2024-104",
                subtotal=Decimal("11200.00"),
                tax_amount=Decimal("800.00"),
                shipping_amount=Decimal("0.00"),
                remit_to_address="500 Tech Blvd, San Jose, CA 95134",
                bill_to_address=bill_to,
                approval_date=date(2024, 3, 22),
                approved_by="Frank CFO",
                gl_account="1500",
                cost_center="ENG",
            ),
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-006",
                vendor_name="Legal Partners LLP",
                vendor_id=v_legal.id,
                description="Legal consultation - Q1",
                amount=Decimal("4500.00"),
                currency="USD",
                invoice_date=date(2024, 4, 1),
                received_date=date(2024, 4, 2),
                due_date=date(2024, 5, 15),
                payment_terms="Net 45",
                status="new",
                po_number="PO-2024-105",
                subtotal=Decimal("4500.00"),
                tax_amount=Decimal("0.00"),
                remit_to_address="200 Legal Ave, New York, NY 10001",
                gl_account="6500",
                cost_center="LEGAL",
            ),
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-007",
                vendor_name="Catering Solutions",
                vendor_id=v_catering.id,
                description="Company event catering",
                amount=Decimal("2800.00"),
                currency="USD",
                invoice_date=date(2024, 3, 25),
                received_date=date(2024, 3, 26),
                due_date=date(2024, 4, 18),
                payment_terms="2/10 Net 30",
                status="ready_for_review",
                po_number="PO-2024-106",
                subtotal=Decimal("2600.00"),
                tax_amount=Decimal("200.00"),
                discount_amount=Decimal("52.00"),
                bill_to_address=bill_to,
                notes="Early payment discount available",
                gl_account="6600",
                cost_center="HR",
            ),
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-008",
                vendor_name="Transport Logistics",
                vendor_id=v_transport.id,
                description="Freight shipping - March",
                amount=Decimal("6300.00"),
                currency="USD",
                invoice_date=date(2024, 3, 30),
                received_date=date(2024, 4, 1),
                due_date=date(2024, 5, 5),
                payment_terms="Net 30",
                status="approved",
                po_number="PO-2024-107",
                subtotal=Decimal("5800.00"),
                tax_amount=Decimal("500.00"),
                shipping_amount=Decimal("0.00"),
                remit_to_address="800 Freight Way, Chicago, IL 60601",
                bill_to_address=bill_to,
                approval_date=date(2024, 4, 2),
                approved_by="Marcus Manager",
                gl_account="6700",
                cost_center="OPS",
            ),
            # Invoice from unverified vendor
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-009",
                vendor_name="Global Printing Services",
                vendor_id=v_unverified.id,
                description="Business card printing - 500 units",
                amount=Decimal("450.00"),
                currency="USD",
                invoice_date=date(2024, 4, 3),
                received_date=date(2024, 4, 4),
                due_date=date(2024, 5, 3),
                payment_terms="Net 30",
                status="new",
                subtotal=Decimal("450.00"),
                tax_amount=Decimal("0.00"),
                vendor_address="42 Print Lane, Portland, OR 97201",
                gl_account="6100",
                cost_center="ADMIN",
            ),
            # Posted in ERP, ready for payment
            Invoice(
                organization_id=org_id,
                invoice_number="INV-2024-010",
                vendor_name="Office Supplies Co",
                vendor_id=v_office.id,
                description="Printer toner and paper",
                amount=Decimal("890.00"),
                currency="USD",
                invoice_date=date(2024, 4, 5),
                received_date=date(2024, 4, 5),
                due_date=date(2024, 5, 5),
                payment_terms="Net 30",
                status="approved",
                po_number="PO-2024-108",
                subtotal=Decimal("820.00"),
                tax_amount=Decimal("70.00"),
                bill_to_address=bill_to,
                approval_date=date(2024, 4, 6),
                approved_by="Marcus Manager",
                gl_account="6100",
                cost_center="ADMIN",
            ),
        ]
        session.add_all(invoices)
        await session.flush()

        # Purchase Orders — linked to vendors, some match invoices
        po1 = PurchaseOrder(
            organization_id=org_id,
            po_number="PO-2024-100",
            vendor_id=v_office.id,
            total=Decimal("1250.00"),
            status="open",
        )
        po2 = PurchaseOrder(
            organization_id=org_id,
            po_number="PO-2024-101",
            vendor_id=v_cloud.id,
            total=Decimal("8500.00"),
            status="open",
        )
        po3 = PurchaseOrder(
            organization_id=org_id,
            po_number="PO-2024-102",
            vendor_id=v_facility.id,
            total=Decimal("3000.00"),
            status="open",
        )  # slight mismatch with invoice
        po4 = PurchaseOrder(
            organization_id=org_id,
            po_number="PO-2024-104",
            vendor_id=v_tech.id,
            total=Decimal("12000.00"),
            status="open",
        )
        po5 = PurchaseOrder(
            organization_id=org_id,
            po_number="PO-2024-107",
            vendor_id=v_transport.id,
            total=Decimal("6300.00"),
            status="open",
        )
        session.add_all([po1, po2, po3, po4, po5])
        await session.flush()

        # PO Line Items
        session.add_all(
            [
                POLineItem(
                    po_id=po1.id,
                    description="Paper and toner",
                    quantity=Decimal("10"),
                    unit_price=Decimal("85.00"),
                    total=Decimal("850.00"),
                ),
                POLineItem(
                    po_id=po1.id,
                    description="Pens and stationery",
                    quantity=Decimal("50"),
                    unit_price=Decimal("8.00"),
                    total=Decimal("400.00"),
                ),
                POLineItem(
                    po_id=po2.id,
                    description="Cloud hosting Q1",
                    quantity=Decimal("1"),
                    unit_price=Decimal("8500.00"),
                    total=Decimal("8500.00"),
                ),
                POLineItem(
                    po_id=po3.id,
                    description="Building cleaning",
                    quantity=Decimal("1"),
                    unit_price=Decimal("2000.00"),
                    total=Decimal("2000.00"),
                ),
                POLineItem(
                    po_id=po3.id,
                    description="HVAC maintenance",
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000.00"),
                    total=Decimal("1000.00"),
                ),
                POLineItem(
                    po_id=po4.id,
                    description="Laptop - Model X",
                    quantity=Decimal("5"),
                    unit_price=Decimal("2400.00"),
                    total=Decimal("12000.00"),
                ),
                POLineItem(
                    po_id=po5.id,
                    description="Freight shipping",
                    quantity=Decimal("1"),
                    unit_price=Decimal("6300.00"),
                    total=Decimal("6300.00"),
                ),
            ]
        )

        # Goods Receipts — for 3-way matching
        gr1 = GoodsReceipt(
            organization_id=org_id,
            gr_number="GR-2024-001",
            po_id=po4.id,
            received_date=date(2024, 3, 22),
            status="received",
        )
        gr2 = GoodsReceipt(
            organization_id=org_id,
            gr_number="GR-2024-002",
            po_id=po5.id,
            received_date=date(2024, 4, 1),
            status="received",
        )
        session.add_all([gr1, gr2])
        await session.flush()

        # GR Line Items — partial receipt on po4 (only 4 of 5 laptops)
        session.add_all(
            [
                GRLineItem(
                    gr_id=gr1.id, description="Laptop - Model X", quantity_received=Decimal("4")
                ),  # ordered 5, received 4
                GRLineItem(
                    gr_id=gr2.id, description="Freight shipping", quantity_received=Decimal("1")
                ),
            ]
        )

        # Extraction results — simulate completed extractions for some invoices
        extraction_results: list[InvoiceExtractionResult] = []
        for inv in invoices[:4]:
            extraction_results.append(
                InvoiceExtractionResult(
                    invoice_id=inv.id,
                    method="claude_vision",
                    confidence=Decimal("0.9500"),
                    raw_result={
                        "provider": "claude_vision",
                        "overall_confidence": 0.95,
                        "fields_extracted": 15,
                    },
                )
            )
        session.add_all(extraction_results)

        # Demo priors_metadata so the "Extraction priors" UI has something to
        # show on seeded data. Real production extractions build this during
        # services/extraction.run_extraction.
        if len(extraction_results) >= 3:
            extraction_results[2].priors_metadata = {
                "vendor_cache_applied": ["currency", "payment_terms"],
            }
        if len(extraction_results) >= 4 and len(invoices) >= 2:
            extraction_results[3].priors_metadata = {
                "vendor_cache_applied": ["tax_rate"],
                "rag_neighbors": [
                    {
                        "invoice_id": str(invoices[0].id),
                        "similarity": 0.91,
                        "vendor_name": invoices[0].vendor_name,
                        "invoice_number": invoices[0].invoice_number,
                        "amount": str(invoices[0].amount),
                    },
                    {
                        "invoice_id": str(invoices[1].id),
                        "similarity": 0.78,
                        "vendor_name": invoices[1].vendor_name,
                        "invoice_number": invoices[1].invoice_number,
                        "amount": str(invoices[1].amount),
                    },
                ],
            }

        # RAG embeddings — one per invoice, using the mock adapter so seed
        # doesn't need an OpenAI key. Lets the /api/extract flow retrieve
        # neighbors on a brand-new invoice upload against the demo tenant.
        from app.services.embedding_adapters.mock_adapter import MockEmbeddingAdapter

        embedder = MockEmbeddingAdapter({"dimensions": 1536})
        for inv in invoices:
            canonical = (
                f"Vendor: {inv.vendor_name}\n"
                f"Invoice #: {inv.invoice_number}\n"
                f"Amount: {inv.amount}\n"
                f"Currency: {inv.currency or 'USD'}\n"
                f"Terms: {inv.payment_terms or ''}\n"
                f"Description: {inv.description or ''}\n"
            )
            emb_result = await embedder.embed(canonical)
            session.add(
                InvoiceEmbedding(
                    invoice_id=inv.id,
                    vendor_id=inv.vendor_id,
                    embedding=emb_result.vector,
                    corrected_fields={
                        "vendor_name": inv.vendor_name,
                        "invoice_number": inv.invoice_number,
                        "amount": str(inv.amount),
                        "currency": inv.currency,
                        "payment_terms": inv.payment_terms,
                    },
                    model="mock",
                )
            )

        # Invoice line items — for the first two invoices
        session.add_all(
            [
                InvoiceLineItem(
                    invoice_id=invoices[0].id,
                    line_number=1,
                    description="Paper and toner",
                    quantity=Decimal("10"),
                    unit_price=Decimal("85.00"),
                    total=Decimal("850.00"),
                    gl_account="6100",
                ),
                InvoiceLineItem(
                    invoice_id=invoices[0].id,
                    line_number=2,
                    description="Pens and stationery",
                    quantity=Decimal("50"),
                    unit_price=Decimal("8.00"),
                    total=Decimal("400.00"),
                    gl_account="6100",
                ),
                InvoiceLineItem(
                    invoice_id=invoices[1].id,
                    line_number=1,
                    description="Cloud hosting Q1",
                    quantity=Decimal("1"),
                    unit_price=Decimal("8500.00"),
                    total=Decimal("8500.00"),
                    gl_account="6200",
                ),
            ]
        )

        # Extraction usage — simulate billing data
        for i, inv in enumerate(invoices[:6]):
            session.add(
                ExtractionUsage(
                    invoice_id=inv.id,
                    provider="claude_vision" if i < 4 else "mock",
                    program_type="platform" if i < 4 else "byok",
                    period="2024-03" if i < 3 else "2024-04",
                    success=True,
                    organization_id=org_id,
                )
            )

        # GL Accounts (Chart of Accounts)
        gl_accounts = [
            GLAccount(
                organization_id=org_id,
                code="1000",
                name="Cash and Cash Equivalents",
                account_type="asset",
                erp_account_id="1000",
            ),
            GLAccount(
                organization_id=org_id,
                code="1200",
                name="Accounts Receivable",
                account_type="asset",
                erp_account_id="1200",
            ),
            GLAccount(
                organization_id=org_id,
                code="1500",
                name="Fixed Assets - Equipment",
                account_type="asset",
                erp_account_id="1500",
            ),
            GLAccount(
                organization_id=org_id,
                code="2000",
                name="Accounts Payable",
                account_type="liability",
                erp_account_id="2000",
            ),
            GLAccount(
                organization_id=org_id,
                code="2100",
                name="Accrued Liabilities",
                account_type="liability",
                erp_account_id="2100",
            ),
            GLAccount(
                organization_id=org_id,
                code="4000",
                name="Revenue - Services",
                account_type="revenue",
                erp_account_id="4000",
            ),
            GLAccount(
                organization_id=org_id,
                code="6100",
                name="Office Supplies & Expenses",
                account_type="expense",
                erp_account_id="6100",
            ),
            GLAccount(
                organization_id=org_id,
                code="6200",
                name="Software & Cloud Services",
                account_type="expense",
                erp_account_id="6200",
            ),
            GLAccount(
                organization_id=org_id,
                code="6300",
                name="Facilities & Maintenance",
                account_type="expense",
                erp_account_id="6300",
            ),
            GLAccount(
                organization_id=org_id,
                code="6400",
                name="Marketing & Advertising",
                account_type="expense",
                erp_account_id="6400",
            ),
            GLAccount(
                organization_id=org_id,
                code="6500",
                name="Legal & Professional Fees",
                account_type="expense",
                erp_account_id="6500",
            ),
            GLAccount(
                organization_id=org_id,
                code="6600",
                name="Meals & Entertainment",
                account_type="expense",
                erp_account_id="6600",
            ),
            GLAccount(
                organization_id=org_id,
                code="6700",
                name="Shipping & Freight",
                account_type="expense",
                erp_account_id="6700",
            ),
            GLAccount(
                organization_id=org_id,
                code="6800",
                name="Travel & Transportation",
                account_type="expense",
                erp_account_id="6800",
            ),
            GLAccount(
                organization_id=org_id,
                code="8000",
                name="Payroll Expense",
                account_type="expense",
                erp_account_id="8000",
            ),
        ]
        session.add_all(gl_accounts)

        # Payment Schedules — for approved invoices
        for inv in invoices:
            if inv.due_date and inv.status in ("approved", "posted_in_erp"):
                discount_date = None
                discount_pct = None
                if inv.payment_terms and "2/10" in (inv.payment_terms or ""):
                    from datetime import timedelta

                    discount_date = (
                        inv.invoice_date + timedelta(days=10) if inv.invoice_date else None
                    )
                    discount_pct = Decimal("2.00")
                session.add(
                    PaymentSchedule(
                        correlation_id=inv.correlation_id,
                        invoice_id=inv.id,
                        due_date=inv.due_date,
                        discount_date=discount_date,
                        discount_percent=discount_pct,
                        payment_terms=inv.payment_terms,
                    )
                )

        # Payment Run — one completed run with payments for the posted_in_erp invoice
        from datetime import datetime

        run = PaymentRun(
            organization_id=org_id,
            status="completed",
            total_amount=Decimal("12000.00"),
            initiated_by=ACME_USER_ID if org_id == ACME_ORG_ID else TECH_USER_ID,
            executed_at=datetime(2024, 3, 25, 14, 0, tzinfo=UTC),
        )
        session.add(run)
        await session.flush()

        # Payment for the posted_in_erp invoice (INV-2024-005)
        session.add(
            Payment(
                correlation_id=invoices[4].correlation_id,
                invoice_id=invoices[4].id,
                payment_run_id=run.id,
                amount=Decimal("12000.00"),
                method="ach",
                status="completed",
                reference="ACH-20240325-001",
            )
        )

        # Payment for the approved transport invoice (INV-2024-008) — pending
        session.add(
            Payment(
                correlation_id=invoices[7].correlation_id,
                invoice_id=invoices[7].id,
                payment_run_id=run.id,
                amount=Decimal("6300.00"),
                method="wire",
                status="pending",
                reference="WIRE-20240326-001",
            )
        )

        # A virtual card payment — for the marketing invoice (INV-2024-004)
        session.add(
            Payment(
                correlation_id=invoices[3].correlation_id,
                invoice_id=invoices[3].id,
                amount=Decimal("5750.00"),
                method="virtual_card",
                status="completed",
                reference="CARD-4242",
            )
        )

        # Exceptions — sample flagged invoices
        session.add_all(
            [
                APException(
                    invoice_id=invoices[2].id,
                    exception_type="po_mismatch",
                    severity="warning",
                    description="Invoice $3,200 vs PO $3,000 — 6.7% variance exceeds 5% tolerance",
                    status="open",
                    organization_id=org_id,
                ),
                APException(
                    invoice_id=invoices[8].id,
                    exception_type="unverified_vendor",
                    severity="warning",
                    description="Invoice linked to unverified vendor: Global Printing Services",
                    status="open",
                    organization_id=org_id,
                ),
                APException(
                    invoice_id=invoices[0].id,
                    exception_type="fraud_flag",
                    severity="info",
                    description="Suspicious round amount: $1,250.00",
                    status="open",
                    organization_id=org_id,
                ),
                APException(
                    invoice_id=invoices[4].id,
                    exception_type="duplicate",
                    severity="warning",
                    description="Duplicate invoice number for Tech Hardware Corp",
                    status="resolved",
                    resolution="Confirmed not a duplicate — different PO",
                    resolved_by="Marcus Manager",
                    organization_id=org_id,
                ),
            ]
        )

        await session.commit()
        print(
            f"  Seeded {tenant_label}: {len(all_vendors)} vendors, {len(invoices)} invoices, "
            f"5 POs, 2 GRs, {len(gl_accounts)} GL accounts, 1 payment run, 3 payments, 4 exceptions"
        )

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
