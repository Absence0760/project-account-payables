"""Oracle NetSuite adapter — direct REST API integration with Token-Based Auth."""

import hashlib
import hmac
import time
import uuid
from urllib.parse import quote

import httpx

from app.services.erp_adapters.base import (
    ErpAdapter,
    ErpInvoiceStatus,
    ErpPostResult,
    InvoicePayload,
)
from app.services.erp_adapters.dispatcher import register_adapter


@register_adapter("netsuite")
class NetSuiteAdapter(ErpAdapter):
    """Direct integration with Oracle NetSuite REST API.

    Required config:
        account_id: NetSuite account ID (e.g. "1234567")
        consumer_key: Integration consumer key
        consumer_secret: Integration consumer secret
        token_id: Token-based auth token ID
        token_secret: Token-based auth token secret
    """

    erp_type = "netsuite"

    def _base_url(self) -> str:
        account = self.config["account_id"].replace("_", "-").lower()
        return f"https://{account}.suitetalk.api.netsuite.com/services/rest/record/v1"

    def _auth_header(self, method: str, url: str) -> str:
        """Generate OAuth 1.0 authorization header for NetSuite TBA."""
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time()))

        params = {
            "oauth_consumer_key": self.config["consumer_key"],
            "oauth_token": self.config["token_id"],
            "oauth_nonce": nonce,
            "oauth_timestamp": timestamp,
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_version": "1.0",
        }

        # Build signature base string
        param_str = "&".join(f"{quote(k)}={quote(v)}" for k, v in sorted(params.items()))
        base_string = f"{method.upper()}&{quote(url, safe='')}&{quote(param_str, safe='')}"

        signing_key = f"{quote(self.config['consumer_secret'])}&{quote(self.config['token_secret'])}"
        signature = hmac.new(
            signing_key.encode(), base_string.encode(), hashlib.sha256
        ).digest()

        import base64
        sig_b64 = base64.b64encode(signature).decode()

        parts = [
            f'OAuth realm="{self.config["account_id"]}"',
            f'oauth_consumer_key="{params["oauth_consumer_key"]}"',
            f'oauth_token="{params["oauth_token"]}"',
            f'oauth_nonce="{nonce}"',
            f'oauth_timestamp="{timestamp}"',
            f'oauth_signature_method="HMAC-SHA256"',
            f'oauth_version="1.0"',
            f'oauth_signature="{quote(sig_b64)}"',
        ]
        return ", ".join(parts)

    async def post_invoice(self, payload: InvoicePayload) -> ErpPostResult:
        url = f"{self._base_url()}/vendorBill"

        body = {
            "tranId": payload.invoice_number,
            "tranDate": payload.invoice_date.isoformat() if payload.invoice_date else None,
            "dueDate": payload.due_date.isoformat() if payload.due_date else None,
            "currency": {"refName": payload.currency},
            "memo": payload.description,
            "externalId": payload.correlation_id,
            "item": {
                "items": [
                    {
                        "description": li.description or "",
                        "quantity": float(li.quantity) if li.quantity else 1,
                        "rate": float(li.unit_price) if li.unit_price else float(li.total or 0),
                        "account": {"refName": li.gl_account} if li.gl_account else None,
                    }
                    for li in payload.line_items
                ] if payload.line_items else [
                    {
                        "description": payload.description or "",
                        "quantity": 1,
                        "rate": float(payload.amount),
                        "account": {"refName": payload.gl_account} if payload.gl_account else None,
                    }
                ],
            },
        }

        headers = {
            "Authorization": self._auth_header("POST", url),
            "Content-Type": "application/json",
            "Prefer": "respond-async",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=headers)

        if resp.status_code in (200, 201, 204):
            # NetSuite returns the record ID in the Location header
            location = resp.headers.get("Location", "")
            doc_id = location.rsplit("/", 1)[-1] if location else None
            return ErpPostResult(
                success=True,
                erp_document_id=doc_id,
                erp_document_number=payload.invoice_number,
                message="Posted to NetSuite",
                raw_response=resp.json() if resp.content else None,
            )
        else:
            return ErpPostResult(
                success=False,
                message=f"NetSuite error {resp.status_code}: {resp.text}",
                raw_response=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None,
            )

    async def get_invoice_status(self, erp_document_id: str) -> ErpInvoiceStatus:
        url = f"{self._base_url()}/vendorBill/{erp_document_id}"
        headers = {
            "Authorization": self._auth_header("GET", url),
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code != 200:
            return ErpInvoiceStatus.unknown

        data = resp.json()
        ns_status = data.get("status", {}).get("refName", "").lower()

        status_map = {
            "open": ErpInvoiceStatus.open,
            "pendingapproval": ErpInvoiceStatus.draft,
            "paidinfull": ErpInvoiceStatus.paid,
            "cancelled": ErpInvoiceStatus.cancelled,
            "voided": ErpInvoiceStatus.cancelled,
        }
        return status_map.get(ns_status, ErpInvoiceStatus.unknown)

    async def void_invoice(self, erp_document_id: str) -> bool:
        # NetSuite uses a "void" transform
        return False

    async def test_connection(self) -> bool:
        try:
            url = f"{self._base_url()}/vendor?limit=1"
            headers = {
                "Authorization": self._auth_header("GET", url),
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
            return resp.status_code == 200
        except Exception:
            return False
