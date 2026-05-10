"""Mock ERP adapter for development and testing."""

import asyncio
import uuid
from decimal import Decimal

from app.services.erp_adapters.base import (
    ErpAdapter,
    ErpInvoiceStatus,
    ErpPostResult,
    InvoicePayload,
    PoLinePayload,
    PoPayload,
)
from app.services.erp_adapters.dispatcher import register_adapter

# Deterministic mock PO catalogue. Used by the /sync-erp endpoint when
# the org is configured against the mock ERP — keeps the demo flow
# working without standing up a real ERP. Vendor names match the seed
# script so vendor linking succeeds out of the box on the acme tenant.
_MOCK_POS: list[PoPayload] = [
    PoPayload(
        po_number="PO-2024-200",
        vendor_name="Office Supplies Co",
        total=Decimal("2500.00"),
        status="open",
        line_items=[
            PoLinePayload(
                description="Printer paper - bulk",
                quantity=Decimal("20"),
                unit_price=Decimal("45.00"),
                total=Decimal("900.00"),
            ),
            PoLinePayload(
                description="Ink cartridges",
                quantity=Decimal("10"),
                unit_price=Decimal("80.00"),
                total=Decimal("800.00"),
            ),
            PoLinePayload(
                description="Desk organizers",
                quantity=Decimal("16"),
                unit_price=Decimal("50.00"),
                total=Decimal("800.00"),
            ),
        ],
    ),
    PoPayload(
        po_number="PO-2024-201",
        vendor_name="Cloud Services Inc",
        total=Decimal("15000.00"),
        status="open",
        line_items=[
            PoLinePayload(
                description="Annual SaaS license",
                quantity=Decimal("1"),
                unit_price=Decimal("12000.00"),
                total=Decimal("12000.00"),
            ),
            PoLinePayload(
                description="Premium support addon",
                quantity=Decimal("1"),
                unit_price=Decimal("3000.00"),
                total=Decimal("3000.00"),
            ),
        ],
    ),
    PoPayload(
        po_number="PO-2024-202",
        vendor_name="Tech Hardware Corp",
        total=Decimal("24000.00"),
        status="open",
        line_items=[
            PoLinePayload(
                description="Laptop Model X Pro",
                quantity=Decimal("10"),
                unit_price=Decimal("2400.00"),
                total=Decimal("24000.00"),
            ),
        ],
    ),
]


@register_adapter("mock")
class MockAdapter(ErpAdapter):
    erp_type = "mock"

    async def post_invoice(self, payload: InvoicePayload) -> ErpPostResult:
        await asyncio.sleep(0.1)  # simulate latency
        doc_id = f"MOCK-{uuid.uuid4().hex[:12].upper()}"
        return ErpPostResult(
            success=True,
            erp_document_id=doc_id,
            erp_document_number=doc_id,
            message="Mock ERP accepted invoice",
        )

    async def get_invoice_status(self, erp_document_id: str) -> ErpInvoiceStatus:
        await asyncio.sleep(0.05)
        return ErpInvoiceStatus.open

    async def void_invoice(self, erp_document_id: str) -> bool:
        await asyncio.sleep(0.05)
        return True

    async def list_pos(self) -> list[PoPayload]:
        await asyncio.sleep(0.05)
        # Return copies so the caller can't mutate the shared catalogue.
        return [
            PoPayload(
                po_number=p.po_number,
                vendor_name=p.vendor_name,
                total=p.total,
                status=p.status,
                line_items=[
                    PoLinePayload(
                        description=li.description,
                        quantity=li.quantity,
                        unit_price=li.unit_price,
                        total=li.total,
                        gl_account=li.gl_account,
                    )
                    for li in p.line_items
                ],
            )
            for p in _MOCK_POS
        ]

    async def test_connection(self) -> bool:
        return True
