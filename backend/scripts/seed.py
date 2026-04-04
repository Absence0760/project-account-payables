"""Seed the database with sample data matching the frontend mock invoices."""

import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from passlib.context import CryptContext
from sqlalchemy import text

from app.database import engine, async_session_factory
from app.models import Base
from app.models.organization import Organization
from app.models.user import User, Role
from app.models.vendor import Vendor
from app.models.invoice import Invoice

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Fixed UUIDs for reproducibility
ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")


async def seed():
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        # Check if already seeded
        result = await session.execute(
            text("SELECT count(*) FROM organizations WHERE id = :id"),
            {"id": ORG_ID},
        )
        if result.scalar() > 0:
            print("Database already seeded. Skipping.")
            return

        # Organization
        org = Organization(id=ORG_ID, name="Acme Corp", slug="acme-corp", plan="pro")
        session.add(org)

        # Roles
        for role_name in ("admin", "ap_manager", "ap_clerk", "cfo"):
            session.add(Role(name=role_name, description=f"{role_name} role"))

        # Demo user  (email: demo@acme.com / password: demo)
        user = User(
            id=USER_ID,
            email="demo@acme.com",
            full_name="Demo User",
            hashed_password=pwd_context.hash("demo"),
            organization_id=ORG_ID,
        )
        session.add(user)

        # Vendors
        vendors = [
            Vendor(organization_id=ORG_ID, name="Office Supplies Co", code="OSC"),
            Vendor(organization_id=ORG_ID, name="Cloud Services Inc", code="CSI"),
            Vendor(organization_id=ORG_ID, name="Facility Services Ltd", code="FSL"),
            Vendor(organization_id=ORG_ID, name="Marketing Agency Pro", code="MAP"),
            Vendor(organization_id=ORG_ID, name="Tech Hardware Corp", code="THC"),
            Vendor(organization_id=ORG_ID, name="Legal Partners LLP", code="LPL"),
            Vendor(organization_id=ORG_ID, name="Catering Solutions", code="CS"),
            Vendor(organization_id=ORG_ID, name="Transport Logistics", code="TL"),
        ]
        session.add_all(vendors)

        # Invoices — mirrors the frontend mock data
        invoices = [
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-001",
                vendor_name="Office Supplies Co",
                description="Monthly office supplies order",
                amount=Decimal("1250.00"),
                currency="USD",
                due_date=date(2024, 4, 15),
                status="new",
                po_number="PO-2024-100",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-002",
                vendor_name="Cloud Services Inc",
                description="Q1 cloud hosting fees",
                amount=Decimal("8500.00"),
                currency="USD",
                due_date=date(2024, 4, 20),
                status="pending",
                po_number="PO-2024-101",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-003",
                vendor_name="Facility Services Ltd",
                description="Building maintenance - March",
                amount=Decimal("3200.00"),
                currency="USD",
                due_date=date(2024, 4, 10),
                status="ready_for_review",
                po_number="PO-2024-102",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-004",
                vendor_name="Marketing Agency Pro",
                description="Social media campaign - February",
                amount=Decimal("5750.00"),
                currency="USD",
                due_date=date(2024, 5, 1),
                status="sent_to_erp",
                po_number="PO-2024-103",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-005",
                vendor_name="Tech Hardware Corp",
                description="Laptop procurement - 5 units",
                amount=Decimal("12000.00"),
                currency="USD",
                due_date=date(2024, 4, 25),
                status="failed",
                po_number="PO-2024-104",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-006",
                vendor_name="Legal Partners LLP",
                description="Legal consultation - Q1",
                amount=Decimal("4500.00"),
                currency="USD",
                due_date=date(2024, 5, 15),
                status="new",
                po_number="PO-2024-105",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-007",
                vendor_name="Catering Solutions",
                description="Company event catering",
                amount=Decimal("2800.00"),
                currency="USD",
                due_date=date(2024, 4, 18),
                status="pending",
                po_number="PO-2024-106",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-008",
                vendor_name="Transport Logistics",
                description="Freight shipping - March",
                amount=Decimal("6300.00"),
                currency="USD",
                due_date=date(2024, 5, 5),
                status="ready_for_review",
                po_number="PO-2024-107",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-009",
                vendor_name="Office Supplies Co",
                description="Printer ink and paper",
                amount=Decimal("450.00"),
                currency="USD",
                due_date=date(2024, 4, 30),
                status="new",
                po_number="PO-2024-108",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-010",
                vendor_name="Cloud Services Inc",
                description="Additional storage allocation",
                amount=Decimal("1800.00"),
                currency="USD",
                due_date=date(2024, 5, 10),
                status="pending",
                po_number="PO-2024-109",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-011",
                vendor_name="Facility Services Ltd",
                description="HVAC system repair",
                amount=Decimal("7200.00"),
                currency="USD",
                due_date=date(2024, 4, 22),
                status="failed",
                po_number="PO-2024-110",
            ),
            Invoice(
                organization_id=ORG_ID,
                invoice_number="INV-2024-012",
                vendor_name="Marketing Agency Pro",
                description="Brand redesign project",
                amount=Decimal("15000.00"),
                currency="USD",
                due_date=date(2024, 6, 1),
                status="sent_to_erp",
                po_number="PO-2024-111",
            ),
        ]
        session.add_all(invoices)

        await session.commit()
        print(f"Seeded: 1 org, 1 user (demo@acme.com / demo), {len(vendors)} vendors, {len(invoices)} invoices")


if __name__ == "__main__":
    asyncio.run(seed())
