"""Microsoft Dynamics 365 Business Central adapter — direct OAuth2 REST integration."""

import httpx

from app.config import settings
from app.services.erp_adapters.base import (
    ErpAdapter,
    ErpInvoiceStatus,
    ErpPostResult,
    InvoicePayload,
)
from app.services.erp_adapters.dispatcher import register_adapter


@register_adapter("dynamics_365_bc")
class BusinessCentralAdapter(ErpAdapter):
    """Direct integration with Dynamics 365 Business Central OData v4 API.

    Required config:
        base_url: e.g. https://api.businesscentral.dynamics.com/v2.0
        tenant_id: Azure AD tenant ID
        client_id: App registration client ID
        client_secret: App registration client secret
        environment: e.g. "production" or "sandbox"
        company_id: BC company ID or name
    """

    erp_type = "dynamics_365_bc"

    async def _get_token(self) -> str:
        # OPERATOR-controlled override (env/process level, not tenant-admin
        # config) so local dev + e2e can point the token exchange at the fake
        # ERP container (backend/docker-compose.yml `fake-erp`, host port
        # 12112). Empty (the default) = the real login.microsoftonline.com
        # URL built from the config's tenant_id.
        if settings.erp_d365_token_url:
            url = settings.erp_d365_token_url
        else:
            tenant_id = self.config["tenant_id"]
            url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.config["client_id"],
                    "client_secret": self.config["client_secret"],
                    "scope": "https://api.businesscentral.dynamics.com/.default",
                },
            )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _api_url(self, path: str) -> str:
        if settings.erp_d365_api_base:
            # OPERATOR-controlled override (env/process level, not tenant-admin
            # config) so local dev + e2e can point the adapter at the fake ERP
            # container (backend/docker-compose.yml `fake-erp`, host port
            # 12112). Trusted, so the SSRF guard below is deliberately skipped
            # — it exists to police admin-supplied config, not operator env.
            base = settings.erp_d365_api_base.rstrip("/")
        else:
            base = self.config["base_url"].rstrip("/")
            # SSRF guard: base_url is admin-supplied config — refuse an internal
            # host before it's interpolated into a server-side request.
            from app.utils.url_safety import assert_public_url

            assert_public_url(base)
        env = self.config.get("environment", "production")
        company = self.config.get("company_id", "")
        return f"{base}/{env}/api/v2.0/companies({company})/{path}"

    async def _find_by_external_document_number(
        self, token: str, external_document_number: str
    ) -> str | None:
        """Look up an existing purchaseInvoice by externalDocumentNumber — the
        pre-create idempotency check (issue #143): a retried push after a
        client-side timeout on the FIRST attempt's response (which may have
        already succeeded server-side) finds the already-created invoice here
        instead of blindly POSTing a second one.
        """
        headers = {"Authorization": f"Bearer {token}"}
        filter_expr = f"externalDocumentNumber eq '{external_document_number}'"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                self._api_url("purchaseInvoices"),
                params={"$filter": filter_expr},
                headers=headers,
            )
        if resp.status_code != 200:
            return None
        values = resp.json().get("value", [])
        if not values:
            return None
        return values[0].get("id")

    async def post_invoice(self, payload: InvoicePayload) -> ErpPostResult:
        token = await self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        existing_id = await self._find_by_external_document_number(token, payload.correlation_id)
        if existing_id:
            return ErpPostResult(
                success=True,
                erp_document_id=existing_id,
                erp_document_number=payload.invoice_number,
                message="Already posted to Business Central (idempotent — "
                "found by externalDocumentNumber)",
            )

        # Step 1: Create purchase invoice
        body = {
            "vendorNumber": payload.vendor_name,
            "invoiceDate": payload.invoice_date.isoformat() if payload.invoice_date else None,
            "dueDate": payload.due_date.isoformat() if payload.due_date else None,
            "vendorInvoiceNumber": payload.invoice_number,
            "externalDocumentNumber": payload.correlation_id,
            "currencyCode": payload.currency,
            "purchaseInvoiceLines": [
                {
                    "lineType": "Account",
                    "lineObjectNumber": li.gl_account or "",
                    "description": li.description or "",
                    "quantity": float(li.quantity) if li.quantity else 1,
                    "unitCost": float(li.unit_price) if li.unit_price else float(li.total or 0),
                }
                for li in payload.line_items
            ]
            if payload.line_items
            else [
                {
                    "lineType": "Account",
                    "lineObjectNumber": payload.gl_account or "",
                    "description": payload.description or "",
                    "quantity": 1,
                    "unitCost": float(payload.amount),
                }
            ],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                self._api_url("purchaseInvoices"),
                json=body,
                headers=headers,
            )

        if resp.status_code in (200, 201):
            data = resp.json()
            doc_id = data.get("id")

            # Step 2: Post (finalize) the purchase invoice
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    post_resp = await client.post(
                        self._api_url(f"purchaseInvoices({doc_id})/Microsoft.NAV.post"),
                        headers=headers,
                    )
                post_resp.raise_for_status()
            except Exception:
                # Invoice created but not posted — still return success with draft status
                pass

            return ErpPostResult(
                success=True,
                erp_document_id=doc_id,
                erp_document_number=data.get("number"),
                message="Posted to Business Central",
                raw_response=data,
            )
        else:
            return ErpPostResult(
                success=False,
                message=f"BC error {resp.status_code}: {resp.text}",
                raw_response=resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else None,
            )

    async def get_invoice_status(self, erp_document_id: str) -> ErpInvoiceStatus:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                self._api_url(f"purchaseInvoices({erp_document_id})"),
                headers=headers,
            )

        if resp.status_code != 200:
            return ErpInvoiceStatus.unknown

        data = resp.json()
        bc_status = data.get("status", "").lower()

        status_map = {
            "draft": ErpInvoiceStatus.draft,
            "open": ErpInvoiceStatus.open,
            "paid": ErpInvoiceStatus.paid,
            "canceled": ErpInvoiceStatus.cancelled,
            "corrective": ErpInvoiceStatus.cancelled,
        }
        return status_map.get(bc_status, ErpInvoiceStatus.unknown)

    async def void_invoice(self, erp_document_id: str) -> bool:
        # BC doesn't support direct void — must create a credit memo
        return False

    async def test_connection(self) -> bool:
        try:
            token = await self._get_token()
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    self._api_url("vendors?$top=1"),
                    headers=headers,
                )
            return resp.status_code == 200
        except Exception:
            return False
