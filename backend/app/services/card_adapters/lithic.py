"""Lithic card adapter — default provider for US/UK/EU markets.

Lithic offers interchange sharing from day one with simple API integration.
Docs: https://docs.lithic.com
"""

import httpx

from app.services.card_adapters.base import (
    CardAdapter,
    CardDetails,
    CardResult,
    CardStatus,
    VirtualCardPayload,
)
from app.services.card_adapters.dispatcher import register_card_adapter

LITHIC_API_BASE = "https://api.lithic.com/v1"
LITHIC_SANDBOX_BASE = "https://sandbox.lithic.com/v1"


@register_card_adapter("lithic")
class LithicAdapter(CardAdapter):
    """Lithic virtual card integration.

    Required config:
        api_key: Lithic API key
        sandbox: bool (default False) — use sandbox environment
    """

    provider_name = "lithic"
    supported_regions = ["US", "UK", "GB", "DE", "FR", "NL", "IE", "ES", "IT"]

    def _base_url(self) -> str:
        if self.config.get("sandbox", False):
            return LITHIC_SANDBOX_BASE
        return LITHIC_API_BASE

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"api-key {self.config['api_key']}",
            "Content-Type": "application/json",
        }

    async def create_card(self, payload: VirtualCardPayload) -> CardResult:
        body = {
            "type": "SINGLE_USE",
            "spend_limit": int(payload.amount * 100),  # cents
            "spend_limit_duration": "TRANSACTION",
            "state": "OPEN",
            "memo": f"{payload.vendor_name} - {payload.description or payload.correlation_id}",
            "metadata": {
                "correlation_id": payload.correlation_id,
                "invoice_id": payload.invoice_id,
                "vendor_name": payload.vendor_name,
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base_url()}/cards",
                json=body,
                headers=self._headers(),
            )

        if resp.status_code in (200, 201):
            data = resp.json()
            return CardResult(
                success=True,
                provider_card_id=data["token"],
                last_four=data.get("last_four", ""),
                message="Card created via Lithic",
                raw_response=data,
            )
        else:
            return CardResult(
                success=False,
                message=f"Lithic error {resp.status_code}: {resp.text}",
            )

    async def get_card_details(self, provider_card_id: str) -> CardDetails:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self._base_url()}/cards/{provider_card_id}",
                headers=self._headers(),
                params={"include_sensitive": "true"},
            )

        resp.raise_for_status()
        data = resp.json()

        return CardDetails(
            card_number=data["pan"],
            exp_month=int(data["exp_month"]),
            exp_year=int(data["exp_year"]),
            cvv=data["cvv"],
            last_four=data.get("last_four", data["pan"][-4:]),
        )

    async def cancel_card(self, provider_card_id: str) -> bool:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.patch(
                f"{self._base_url()}/cards/{provider_card_id}",
                json={"state": "CLOSED"},
                headers=self._headers(),
            )
        return resp.status_code == 200

    async def get_card_status(self, provider_card_id: str) -> CardStatus:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self._base_url()}/cards/{provider_card_id}",
                headers=self._headers(),
            )

        if resp.status_code != 200:
            return CardStatus.created

        data = resp.json()
        state = data.get("state", "").upper()
        data.get("spend_limit", 0)
        pending = data.get("pending_amount", 0)

        if state == "CLOSED":
            return CardStatus.cancelled
        if state == "PAUSED":
            return CardStatus.expired
        if pending > 0:
            return CardStatus.charged
        return CardStatus.active

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._base_url()}/cards?page_size=1",
                    headers=self._headers(),
                )
            return resp.status_code == 200
        except Exception:
            return False
