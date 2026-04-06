"""Mock ERP adapter for development and testing."""

import asyncio
import uuid

from app.services.erp_adapters.base import (
    ErpAdapter,
    ErpInvoiceStatus,
    ErpPostResult,
    InvoicePayload,
)
from app.services.erp_adapters.dispatcher import register_adapter


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

    async def test_connection(self) -> bool:
        return True
