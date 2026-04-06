"""Merge.dev unified API adapter — one integration for all ERPs."""

import httpx

from app.services.erp_adapters.base import (
    ErpAdapter,
    ErpInvoiceStatus,
    ErpPostResult,
    InvoicePayload,
)
from app.services.erp_adapters.dispatcher import register_adapter

MERGE_API_BASE = "https://api.merge.dev/api/accounting/v1"


@register_adapter("merge_dev")
class MergeDevAdapter(ErpAdapter):
    """Posts invoices through Merge.dev's unified accounting API.

    Required config:
        api_key: Merge.dev API key (from your Merge dashboard)
        account_token: Per-customer linked account token (created when
                       the customer connects their ERP via Merge Link)
    """

    erp_type = "merge_dev"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config['api_key']}",
            "X-Account-Token": self.config["account_token"],
            "Content-Type": "application/json",
        }

    async def post_invoice(self, payload: InvoicePayload) -> ErpPostResult:
        body = {
            "model": {
                "type": "ACCOUNTS_PAYABLE",
                "number": payload.invoice_number,
                "issue_date": payload.invoice_date.isoformat() if payload.invoice_date else None,
                "due_date": payload.due_date.isoformat() if payload.due_date else None,
                "currency": payload.currency,
                "total_amount": float(payload.amount),
                "sub_total": float(payload.subtotal) if payload.subtotal else None,
                "total_tax_amount": float(payload.tax_amount) if payload.tax_amount else None,
                "total_discount": float(payload.discount_amount) if payload.discount_amount else None,
                "memo": payload.description,
                "purchase_order_number": payload.po_number,
                "line_items": [
                    {
                        "description": li.description,
                        "quantity": float(li.quantity) if li.quantity else None,
                        "unit_price": float(li.unit_price) if li.unit_price else None,
                        "total_line_amount": float(li.total) if li.total else None,
                        "account": li.gl_account,
                    }
                    for li in payload.line_items
                ],
                "integration_params": {
                    "correlation_id": payload.correlation_id,
                },
            }
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{MERGE_API_BASE}/invoices",
                json=body,
                headers=self._headers(),
            )

        if resp.status_code in (200, 201):
            data = resp.json()
            model = data.get("model", {})
            return ErpPostResult(
                success=True,
                erp_document_id=model.get("id"),
                erp_document_number=model.get("number"),
                message="Posted via Merge.dev",
                raw_response=data,
            )
        else:
            return ErpPostResult(
                success=False,
                message=f"Merge.dev error {resp.status_code}: {resp.text}",
                raw_response=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None,
            )

    async def get_invoice_status(self, erp_document_id: str) -> ErpInvoiceStatus:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{MERGE_API_BASE}/invoices/{erp_document_id}",
                headers=self._headers(),
            )

        if resp.status_code != 200:
            return ErpInvoiceStatus.unknown

        data = resp.json()
        status = data.get("status", "").upper()

        status_map = {
            "DRAFT": ErpInvoiceStatus.draft,
            "SUBMITTED": ErpInvoiceStatus.open,
            "OPEN": ErpInvoiceStatus.open,
            "PARTIALLY_PAID": ErpInvoiceStatus.partially_paid,
            "PAID": ErpInvoiceStatus.paid,
            "VOIDED": ErpInvoiceStatus.cancelled,
        }
        return status_map.get(status, ErpInvoiceStatus.unknown)

    async def void_invoice(self, erp_document_id: str) -> bool:
        # Merge.dev doesn't support direct voiding — depends on ERP capability
        return False

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{MERGE_API_BASE}/account-details",
                    headers=self._headers(),
                )
            return resp.status_code == 200
        except Exception:
            return False
