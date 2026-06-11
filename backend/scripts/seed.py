"""Seed the database with sample data for two demo tenants.

Two seed shapes:

- **Full** (default) — every tenant (acme + techflow + e2e1..e2eN) gets
  the rich demo dataset: 9 vendors, ~50 invoices across every status,
  payment runs, exceptions, PO matching scenarios, virtual cards, etc.
  Best for local development where you actually want to click around.
- **Lean** (``--lean`` / ``AP_SEED_MODE=lean``) — every tenant gets
  just enough for the e2e suite (4 vendors, 10 invoices across status
  buckets, 1 default workflow, 1 GL account, no exceptions/payments/
  cards). Specs that need richer state build it via API at the start
  of the test. Roughly an order of magnitude faster — that's what
  ``.github/workflows/ci.yml`` runs.
"""

import argparse
import asyncio
import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import ROLE_ADMIN, ROLE_AP_CLERK, ROLE_AP_MANAGER, ROLE_CFO
from app.config import settings
from app.database import _make_tenant_url, control_engine, control_session_factory
from app.models import Base
from app.models.credit_memo import CreditMemo
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
from app.models.vendor_user import VendorUser
from app.models.workflow import AuditLog, WorkflowDefinition
from app.utils.passwords import pwd_context

# Canonical role set seeded into every control plane, keyed by the same
# ROLE_* constants `app.api.deps` composes into ALL_ROLES. Building the Role
# rows from this map means the seeded roles and the `require_roles` allow-list
# cannot drift — `tests/test_rbac.py` asserts the two sets are identical.
ROLE_DEFINITIONS: dict[str, str] = {
    ROLE_ADMIN: "Full access to all features and user management",
    ROLE_AP_MANAGER: "Review and approve invoices",
    ROLE_AP_CLERK: "Upload invoices and enter data",
    ROLE_CFO: "Approve high-value invoices and view reports",
}

# Fixed UUIDs for reproducibility
ACME_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ACME_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
TECH_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
TECH_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")

# Per-worker e2e tenants. Playwright workers map (workerIndex % N) → e2eN, so
# each parallel worker owns its own tenant DB and can mutate freely without
# colliding with peers. Default N=4; bump via AP_E2E_TENANT_COUNT for higher
# parallelism. Setting it to 0 skips the e2e seed entirely.
E2E_TENANT_COUNT = int(os.environ.get("AP_E2E_TENANT_COUNT", "4"))

CONTROL_TABLES = {
    "organizations",
    "users",
    "roles",
    "user_roles",
    "email_verifications",
}

# Supplier-portal demo credential. One VendorUser is seeded per tenant
# (full + lean) tied to a known seeded vendor, so the Playwright
# `tests-e2e/portal/` suite has a real account to sign in with. The email
# is namespaced per tenant DB because `VendorUser.email` is globally
# UNIQUE within a tenant DB — but the same literal works across tenants
# since each tenant has its own DB. The password matches the rest of the
# seed ("demo") and skips `validate_password_complexity` because the seed
# hashes directly. `must_change_password=False` so login lands straight on
# `/portal/invoices`; the portal spec exercises the must-change redirect by
# flipping the flag via `tenantPsql`.
PORTAL_USER_EMAIL = "supplier@portal.test"
PORTAL_USER_PASSWORD = "demo"


def _make_portal_user(vendor: "Vendor") -> "VendorUser":
    """Build a supplier-portal user bound to ``vendor``.

    Caller must have flushed ``vendor`` so ``vendor.id`` is populated, then
    add the returned row to the session and commit.
    """
    return VendorUser(
        vendor_id=vendor.id,
        email=PORTAL_USER_EMAIL,
        full_name="Portal Demo User",
        hashed_password=pwd_context.hash(PORTAL_USER_PASSWORD),
        is_active=True,
        must_change_password=False,
    )


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

        # Orgs. The two tenants intentionally diverge on settings so a
        # local "compare-the-two-tabs" demo exercises the org-config
        # surface (card expiry, payment provider) without scripts.
        session.add(
            Organization(
                id=ACME_ORG_ID,
                name="Acme Corp",
                slug="acme",
                plan="pro",
                db_name="ap_acme",
                settings={
                    "cards": {
                        "enabled": True,
                        "program_type": "platform",
                        "region": "US",
                        # Default 30 days — exercises the no-override path
                        # in `_resolve_card_config`.
                    },
                    "payments": {"provider": "mock"},
                },
            )
        )
        session.add(
            Organization(
                id=TECH_ORG_ID,
                name="TechFlow Inc",
                slug="techflow",
                plan="pro",
                db_name="ap_techflow",
                settings={
                    "cards": {
                        "enabled": True,
                        "program_type": "platform",
                        "region": "US",
                        # 14-day expiry exercises the org-override path
                        # — proves `default_expiry_days` propagates from
                        # settings → resolver → adapter payload.
                        "default_expiry_days": 14,
                    },
                    "payments": {"provider": "mock"},
                },
            )
        )

        # Roles — built from ROLE_DEFINITIONS so the seeded set stays in
        # lockstep with ALL_ROLES (see tests/test_rbac.py).
        admin_role = Role(name=ROLE_ADMIN, description=ROLE_DEFINITIONS[ROLE_ADMIN])
        ap_manager_role = Role(name=ROLE_AP_MANAGER, description=ROLE_DEFINITIONS[ROLE_AP_MANAGER])
        ap_clerk_role = Role(name=ROLE_AP_CLERK, description=ROLE_DEFINITIONS[ROLE_AP_CLERK])
        cfo_role = Role(name=ROLE_CFO, description=ROLE_DEFINITIONS[ROLE_CFO])
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

        # Default workflow definition — enable extraction + approval +
        # erp_export so the UI's status filter chips render against the
        # full status set the seeded invoices use. The default created
        # lazily by the API has all steps disabled, which would hide
        # most chips and make the demo (and e2e tests) shallow.
        session.add(
            WorkflowDefinition(
                organization_id=org_id,
                name="Default Workflow",
                description="Full pipeline: extraction → approval → ERP export.",
                is_active=True,
                is_default=True,
                steps_config={
                    "steps": [
                        {
                            "number": 1,
                            "type": "extraction",
                            "name": "Data Extraction",
                            "enabled": True,
                            "config": {
                                "auto_approve_enabled": False,
                                "auto_approve_threshold": 0.95,
                            },
                        },
                        {
                            "number": 2,
                            "type": "approval",
                            "name": "Manager Approval",
                            "enabled": True,
                            "config": {
                                "required": True,
                                "approver_id": None,
                                "approver_strategy": "manual",
                                "require_segregation": True,
                            },
                        },
                        {
                            "number": 3,
                            "type": "erp_export",
                            "name": "ERP Export",
                            "enabled": True,
                            "config": {"erp_system": "default"},
                        },
                    ]
                },
            )
        )
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

        # Audit log — at minimum a "created" row per invoice so the
        # /api/invoices/<id>/audit-log endpoint returns something and the
        # InvoiceModal's Activity timeline renders. Matches what the API
        # itself emits via app.services.audit.log_action when an invoice
        # is created or transitioned. Status-transition rows are layered
        # on top for the few invoices that are already past 'new'.
        seed_actor = ACME_USER_ID if org_id == ACME_ORG_ID else TECH_USER_ID
        audit_entries: list[AuditLog] = []
        for inv in invoices:
            audit_entries.append(
                AuditLog(
                    correlation_id=inv.correlation_id,
                    organization_id=org_id,
                    actor_id=seed_actor,
                    action="invoice.created",
                    entity_type="invoice",
                    entity_id=inv.id,
                    details={"source": "seed", "status": inv.status},
                )
            )
            if inv.status in ("ready_for_review", "approved", "posted_in_erp"):
                audit_entries.append(
                    AuditLog(
                        correlation_id=inv.correlation_id,
                        organization_id=org_id,
                        actor_id=seed_actor,
                        action="invoice.status_changed",
                        entity_type="invoice",
                        entity_id=inv.id,
                        details={"from": "new", "to": inv.status},
                    )
                )
        session.add_all(audit_entries)
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
                completed_at=datetime(2024, 3, 25, 14, 30, tzinfo=UTC),
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
                completed_at=datetime(2024, 3, 20, 10, 0, tzinfo=UTC),
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

        # Supplier-portal user — bound to Tech Hardware Corp (v_tech), which
        # owns INV-2024-005 (invoices[4]) and its completed ACH payment. The
        # `tests-e2e/portal/` suite signs in as this user and asserts it sees
        # only v_tech's invoices/payments — every other seeded vendor's rows
        # must stay invisible (tenant + vendor isolation).
        session.add(_make_portal_user(v_tech))

        await session.commit()
        print(
            f"  Seeded {tenant_label}: {len(all_vendors)} vendors, {len(invoices)} invoices, "
            f"5 POs, 2 GRs, {len(gl_accounts)} GL accounts, 1 payment run, 3 payments, "
            f"4 exceptions, 1 portal user"
        )

    await engine.dispose()


def _e2e_org_id(idx: int) -> uuid.UUID:
    """Deterministic UUID per e2e tenant index (1-based).

    Tail format: ``e2e000000<idx:03d>`` (12 hex chars). ``e2e`` is the
    self-documenting prefix marker — every e2e-tenant id eyeballs as
    ``...-e2e000000001`` etc., easy to spot in SQL and log dumps.
    """
    return uuid.UUID(f"00000000-0000-0000-0000-e2e000000{idx:03d}")


def _e2e_user_id(idx: int, role: str) -> uuid.UUID:
    """Deterministic UUID per e2e tenant user. ``role`` is one of
    'admin'/'manager'/'clerk'/'cfo'. Tail format
    ``<role:02d>e2e000<idx:04d>`` keeps the marker in plaintext."""
    role_code = {"admin": 1, "manager": 2, "clerk": 3, "cfo": 4}[role]
    return uuid.UUID(f"00000000-0000-0000-0000-{role_code:02d}e2e000{idx:04d}")


E2E_PASSWORD = "demo"


async def seed_e2e_control_plane(roles: dict[str, "Role"]) -> list[tuple[str, uuid.UUID, str]]:
    """Provision e2e tenant orgs + role-segmented users in the control plane.

    Returns the list of ``(db_name, org_id, label)`` tuples to seed
    tenant-DB rows for. Idempotent: re-running just skips orgs that
    already exist (matched by slug).
    """
    if E2E_TENANT_COUNT <= 0:
        print("  AP_E2E_TENANT_COUNT=0 — skipping e2e tenant seed.")
        return []

    created: list[tuple[str, uuid.UUID, str]] = []
    async with control_session_factory() as session:
        existing = await session.execute(
            text("SELECT slug FROM organizations WHERE slug LIKE 'e2e%'")
        )
        existing_slugs = {r[0] for r in existing.all()}

        hashed = pwd_context.hash(E2E_PASSWORD)

        for i in range(1, E2E_TENANT_COUNT + 1):
            slug = f"e2e{i}"
            db_name = f"ap_{slug}"
            label = f"E2E Worker {i}"
            org_id = _e2e_org_id(i)
            created.append((db_name, org_id, label))

            if slug in existing_slugs:
                print(f"  e2e tenant already seeded: {slug} (skipping)")
                continue

            session.add(
                Organization(
                    id=org_id,
                    name=label,
                    slug=slug,
                    plan="pro",
                    db_name=db_name,
                    settings={
                        "cards": {"enabled": True, "program_type": "platform", "region": "US"},
                        "payments": {"provider": "mock"},
                    },
                )
            )

            # One user per role — Playwright fixtures route signIn to these
            # by deriving the email from the worker's tenant slug.
            users = [
                ("admin", "admin", roles["admin"]),
                ("manager", "Marcus Manager", roles["ap_manager"]),
                ("clerk", "Clara Clerk", roles["ap_clerk"]),
                ("cfo", "Frank CFO", roles["cfo"]),
            ]
            for role_key, full_name, role_row in users:
                u = User(
                    id=_e2e_user_id(i, role_key),
                    email=f"demo+{role_key}@{slug}.localhost",
                    full_name=full_name,
                    hashed_password=hashed,
                    organization_id=org_id,
                )
                session.add(u)
                await session.flush()
                session.add(UserRole(user_id=u.id, role_id=role_row.id))

        await session.commit()

    if created:
        print(f"  Seeded {E2E_TENANT_COUNT} e2e tenant(s) for Playwright workers")
    return created


async def seed_tenant_lean(db_name: str, org_id: uuid.UUID, tenant_label: str):
    """Minimal per-tenant seed for CI / e2e runs.

    Creates just enough fixture data for the Playwright suite:

    - 4 vendors covering the status buckets specs assert against
      (active, unverified, inactive, rejected).
    - 1 GL account (the suite's invoice-edit + bulk-recode specs
      need one to exist in the dropdown).
    - 1 default workflow definition named "Default Workflow" with
      the extraction → approval → erp_export step shape spec
      assertions match against.
    - 10 invoices spread across the status enum so the
      `/invoices` filter chips have at least one row each.
    - 1 PO + 1 GR with line items (for /purchase-orders and
      /goods-receipts list specs).
    - 4 exceptions covering open / resolved / escalated / dismissed
      (for /exceptions filter + resolve specs).
    - 2 credit memos: open + applied (for /credit-memos specs).

    Skips: payment runs, virtual cards, embeddings, audit-log
    fixtures, bank reconciliation, sanctions screening, the rich
    multi-invoice demo narrative — anything a spec needs beyond
    the above it creates on its own via API or ``tenantPsql``.
    Idempotent: bails if vendors already exist.

    Wall-clock target: ~2–3 s per tenant vs ~10–15 s for the full
    seed. The four ``e2e<N>`` tenants times four shards is the
    fattest contributor to CI's e2e setup time, so this is where
    the biggest speed-up lives.
    """
    tenant_url = _make_tenant_url(db_name)
    engine = create_async_engine(tenant_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            result = await session.execute(text("SELECT count(*) FROM vendors"))
            if result.scalar() > 0:
                print(f"  {tenant_label} tenant already seeded. Skipping.")
                return

            v_alpha = Vendor(
                organization_id=org_id,
                name="Lean Vendor Alpha",
                code="LVA",
                email="alpha@vendor.test",
                address="1 Test Lane, Test City, TC 00001",
                tax_id="11-1111111",
                payment_terms="Net 30",
                status="active",
                source="erp_sync",
                erp_vendor_id="ERP-LVA",
                accepts_virtual_cards=True,
            )
            v_beta = Vendor(
                organization_id=org_id,
                name="Lean Vendor Beta",
                code="LVB",
                email="beta@vendor.test",
                payment_terms="Net 60",
                status="unverified",
                source="ai_extracted",
            )
            v_gamma = Vendor(
                organization_id=org_id,
                name="Lean Vendor Gamma",
                code="LVG",
                status="inactive",
                source="manual",
            )
            v_delta = Vendor(
                organization_id=org_id,
                name="Lean Vendor Delta",
                code="LVD",
                status="rejected",
                source="ai_extracted",
            )
            session.add_all([v_alpha, v_beta, v_gamma, v_delta])

            session.add(
                GLAccount(
                    organization_id=org_id,
                    code="6000",
                    name="Operating Expenses",
                    account_type="expense",
                )
            )

            session.add(
                WorkflowDefinition(
                    organization_id=org_id,
                    # `workflows/list.spec.ts` matches on the literal
                    # name "Default Workflow". The acme/techflow full
                    # seed uses the same string — keep parity.
                    name="Default Workflow",
                    is_active=True,
                    is_default=True,
                    # Schema requires a dict at the root with a "steps"
                    # array (`schemas/workflow.py::WorkflowDefinitionResponse`
                    # types `steps_config: dict`). Mirror the canonical
                    # shape `seed_tenant` uses for acme/techflow.
                    steps_config={
                        "steps": [
                            {
                                "number": 1,
                                "type": "extraction",
                                "name": "Data Extraction",
                                "enabled": True,
                                "config": {},
                            },
                            {
                                "number": 2,
                                "type": "approval",
                                "name": "Manager Approval",
                                "enabled": True,
                                # Mirror the canonical config shape
                                # `seed_tenant` uses — the workflow
                                # engine reads `approver_strategy`,
                                # `required`, `require_segregation`.
                                # `workflows/invoice-routing.spec.ts`
                                # patches `auto_approve_below` and
                                # asserts the engine picks it up.
                                "config": {
                                    "required": True,
                                    "approver_id": None,
                                    "approver_strategy": "manual",
                                    "require_segregation": True,
                                },
                            },
                            {
                                "number": 3,
                                "type": "erp_export",
                                "name": "ERP Export",
                                "enabled": True,
                                "config": {"erp_system": "default"},
                            },
                        ],
                    },
                )
            )

            await session.flush()

            # Spread invoices across the status buckets so the
            # `/invoices` page's filter chips each have at least one
            # row. Bias toward `approved` (×5) because the
            # `/payments` queue tests consume queue rows during their
            # execute flow — having a handful in flight keeps the
            # queue populated for the rest of the worker's suite.
            statuses = (
                ["new"]
                + ["pending"]
                + ["ready_for_review"]
                + ["approved"] * 5
                + ["sending_to_erp"]
                + ["sent_to_erp"]
                + ["posted_in_erp"]
                + ["payment_scheduled"]
                + ["paid"]
                + ["rejected"]
            )
            # Bind invoices to a vendor_id so the supplier portal (which
            # filters strictly on `Invoice.vendor_id == vendor_user.vendor_id`)
            # has rows to show. Most go to v_alpha (the portal user's vendor);
            # exactly one — the first, `new`-status row — goes to v_beta so the
            # portal-isolation spec can assert the alpha user never sees it.
            invoices: list[Invoice] = []
            for i, status in enumerate(statuses, start=1):
                owner = v_beta if i == 1 else v_alpha
                # correlation_id is what `GET /api/invoices/{id}/audit-log`
                # joins on. Generate it here so the matching audit_log
                # row stamped below can be retrieved.
                corr_id = uuid.uuid4()
                inv = Invoice(
                    organization_id=org_id,
                    vendor_name=owner.name,
                    vendor_id=owner.id,
                    invoice_number=f"LEAN-{i:03d}",
                    amount=Decimal(f"{100 + i}.00"),
                    currency="USD",
                    status=status,
                    invoice_date=date(2026, 1, i % 28 + 1),
                    correlation_id=corr_id,
                )
                invoices.append(inv)
                session.add(inv)

            # One PO + one GR linked to the PO. `purchase-orders/list.spec.ts`
            # and `goods-receipts/list.spec.ts` each just need the
            # table to render at least one row plus a line-item modal.
            po = PurchaseOrder(
                organization_id=org_id,
                po_number="LEAN-PO-001",
                vendor_id=v_alpha.id,
                total=Decimal("500.00"),
                status="open",
            )
            session.add(po)
            await session.flush()
            session.add_all(
                [
                    POLineItem(
                        po_id=po.id,
                        description="Office paper, 10 reams",
                        quantity=Decimal("10"),
                        unit_price=Decimal("25.00"),
                        total=Decimal("250.00"),
                    ),
                    POLineItem(
                        po_id=po.id,
                        description="Ballpoint pens, 50 ct",
                        quantity=Decimal("50"),
                        unit_price=Decimal("5.00"),
                        total=Decimal("250.00"),
                    ),
                ]
            )

            gr = GoodsReceipt(
                organization_id=org_id,
                gr_number="LEAN-GR-001",
                po_id=po.id,
                received_date=date(2026, 1, 15),
                status="received",
            )
            session.add(gr)
            await session.flush()
            session.add_all(
                [
                    GRLineItem(
                        gr_id=gr.id,
                        description="Office paper, 10 reams",
                        quantity_received=Decimal("10"),
                    ),
                    GRLineItem(
                        gr_id=gr.id,
                        description="Ballpoint pens, 50 ct",
                        quantity_received=Decimal("50"),
                    ),
                ]
            )

            # Four exceptions covering each status the spec set asserts
            # against. Attach each to a distinct seeded invoice so the
            # exception's "open invoices" reference can resolve.
            session.add_all(
                [
                    APException(
                        organization_id=org_id,
                        invoice_id=invoices[0].id,
                        exception_type="duplicate",
                        severity="warning",
                        description="Possible duplicate of LEAN-001",
                        status="open",
                    ),
                    APException(
                        organization_id=org_id,
                        invoice_id=invoices[1].id,
                        exception_type="po_mismatch",
                        severity="warning",
                        description="Invoice total differs from PO",
                        status="resolved",
                        resolution="PO updated to match invoice",
                    ),
                    APException(
                        organization_id=org_id,
                        invoice_id=invoices[2].id,
                        exception_type="fraud_flag",
                        severity="error",
                        description="Vendor flagged for manual review",
                        status="escalated",
                    ),
                    APException(
                        organization_id=org_id,
                        invoice_id=invoices[3].id,
                        exception_type="amount_exceeded",
                        severity="info",
                        description="Amount exceeds approval threshold",
                        status="dismissed",
                    ),
                ]
            )

            # Two credit memos — open + applied. The /credit-memos page
            # asserts both buckets exist for the filter-chip narrowing
            # test.
            session.add_all(
                [
                    CreditMemo(
                        organization_id=org_id,
                        memo_number="CM-LEAN-001",
                        vendor_id=v_alpha.id,
                        amount=Decimal("75.00"),
                        currency="USD",
                        issued_date=date(2026, 1, 5),
                        reason="Damaged goods returned",
                        status="open",
                    ),
                    CreditMemo(
                        organization_id=org_id,
                        memo_number="CM-LEAN-002",
                        vendor_id=v_alpha.id,
                        amount=Decimal("40.00"),
                        currency="USD",
                        issued_date=date(2026, 1, 10),
                        reason="Volume discount",
                        status="applied",
                    ),
                ]
            )

            # One completed payment run with one settled payment, attached
            # to the seeded `paid` invoice. The `/payments` Runs tab +
            # RunDetailModal spec assumes a seeded run row exists. Look the
            # invoice up by status rather than by index — the status list
            # above is reordered/re-weighted as specs need rows, and a
            # hardcoded index silently drifts onto the wrong invoice.
            paid_invoice = next(inv for inv in invoices if inv.status == "paid")
            run = PaymentRun(
                organization_id=org_id,
                status="completed",
                total_amount=paid_invoice.amount,
                executed_at=datetime(2026, 1, 20, tzinfo=UTC),
            )
            session.add(run)
            await session.flush()
            # `paid_invoice` belongs to v_alpha (only invoices[0] is v_beta),
            # so this payment is the portal user's own. The portal-payments
            # isolation spec asserts the alpha user sees this row.
            session.add(
                Payment(
                    payment_run_id=run.id,
                    invoice_id=paid_invoice.id,
                    amount=paid_invoice.amount,
                    method="ach",
                    status="completed",
                    reference="LEAN-PAY-ALPHA",
                    completed_at=datetime(2026, 1, 20, tzinfo=UTC),
                )
            )
            # A second payment on v_beta's invoice (invoices[0]). This must
            # NOT appear in the alpha portal user's payment list — the
            # isolation assertion checks the reference string is absent.
            session.add(
                Payment(
                    payment_run_id=run.id,
                    invoice_id=invoices[0].id,
                    amount=invoices[0].amount,
                    method="wire",
                    status="completed",
                    reference="LEAN-PAY-BETA",
                    completed_at=datetime(2026, 1, 20, tzinfo=UTC),
                )
            )

            # Supplier-portal user bound to v_alpha — the vendor that owns
            # every lean invoice except invoices[0]. The `tests-e2e/portal/`
            # suite signs in as this user and asserts vendor-scoped isolation
            # (sees LEAN-002..LEAN-016 + LEAN-PAY-ALPHA, never LEAN-001 /
            # LEAN-PAY-BETA which belong to v_beta).
            session.add(_make_portal_user(v_alpha))

            # One audit-log row per invoice. `GET /api/invoices/{id}/audit-log`
            # joins on `correlation_id`, not `entity_id` — set the same
            # correlation_id on the audit row as the invoice has so the
            # endpoint actually returns this row. The `/invoices` modal
            # Activity-section test relies on it.
            session.add_all(
                [
                    AuditLog(
                        organization_id=org_id,
                        correlation_id=inv.correlation_id,
                        action="invoice_created",
                        entity_type="invoice",
                        entity_id=inv.id,
                        details={"status": inv.status, "amount": str(inv.amount)},
                    )
                    for inv in invoices
                ]
            )

            await session.commit()
            print(f"  Seeded lean fixtures for {tenant_label} (incl. 1 portal user)")
    finally:
        await engine.dispose()


async def _load_seeded_roles() -> dict[str, "Role"]:
    """Fetch the four role rows seeded by ``seed_control_plane``."""
    async with control_session_factory() as session:
        rows = await session.execute(text("SELECT id, name FROM roles"))
        out: dict[str, Role] = {}
        for rid, name in rows.all():
            out[name] = Role(id=rid, name=name)
        return out


async def seed(lean: bool = False):
    """Provision every tenant DB.

    Parameters
    ----------
    lean : bool
        When ``True``, every tenant gets the minimal fixture set via
        :func:`seed_tenant_lean` instead of the full demo dataset. CI
        flips this on for the e2e job; local dev leaves it off.
    """
    mode_label = "lean" if lean else "full"
    print(f"=== Seeding control plane (mode={mode_label}) ===")
    await create_control_tables()
    await seed_control_plane()

    roles = await _load_seeded_roles()
    e2e_tenants = await seed_e2e_control_plane(roles)

    base_tenants = [
        ("ap_acme", ACME_ORG_ID, "Acme Corp"),
        ("ap_techflow", TECH_ORG_ID, "TechFlow Inc"),
    ]
    tenant_seeder = seed_tenant_lean if lean else seed_tenant
    for db_name, org_id, label in base_tenants + e2e_tenants:
        print(f"\n=== Seeding tenant: {label} ({db_name}) ===")
        await create_database(db_name)
        await create_tenant_tables(db_name)
        await tenant_seeder(db_name, org_id, label)

    await control_engine.dispose()

    print(f"\nDone! {2 + len(e2e_tenants)} tenants ready:")
    print("  acme.localhost:7777    — demo@acme.com")
    print("  techflow.localhost:7777 — admin@techflow.com")
    for db_name, _org_id, label in e2e_tenants:
        slug = db_name.removeprefix("ap_")
        # Don't interpolate the password into stdout — CodeQL flags
        # ``print(...{PASSWORD}...)`` even when the value is the dev
        # default. The credentials are documented in ``frontend/tests-e2e/
        # README.md`` and ``backend/scripts/seed.py`` source.
        print(f"  {slug}.localhost:7777    — demo+admin@{slug}.localhost")
    print("  (Login password for every seeded user is the documented dev default.)")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed local + e2e tenants.")
    parser.add_argument(
        "--lean",
        action="store_true",
        default=os.environ.get("AP_SEED_MODE", "").lower() == "lean",
        help=(
            "Use the minimal per-tenant fixture set. Faster (~10× less work "
            "per tenant); intended for CI's e2e job. Local dev defaults to "
            "the full demo dataset. Also enabled via AP_SEED_MODE=lean."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(seed(lean=args.lean))
