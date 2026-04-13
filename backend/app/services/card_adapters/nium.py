"""Nium card adapter — provider for non-US/UK/EU markets (40+ countries).

Nium offers global card issuance with interchange sharing.
Docs: https://docs.nium.com
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

NIUM_API_BASE = "https://api.nium.com/api/v1"
NIUM_SANDBOX_BASE = "https://sandbox-api.nium.com/api/v1"


@register_card_adapter("nium")
class NiumAdapter(CardAdapter):
    """Nium virtual card integration for global markets.

    Required config:
        client_id: Nium client ID
        client_secret: Nium client secret
        customer_hash_id: Nium customer (your company) hash ID
        wallet_hash_id: Nium wallet hash ID
        sandbox: bool (default False)
    """

    provider_name = "nium"
    supported_regions = [
        "ZA",
        "AU",
        "NZ",
        "SG",
        "HK",
        "IN",
        "CA",
        "BR",
        "MX",
        "AE",
        "JP",
        "KR",
        "MY",
        "TH",
        "PH",
        "ID",
        "VN",
    ]

    _access_token: str | None = None

    def _base_url(self) -> str:
        if self.config.get("sandbox", False):
            return NIUM_SANDBOX_BASE
        return NIUM_API_BASE

    async def _get_token(self) -> str:
        if self._access_token:
            return self._access_token

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self._base_url()}/auth/token",
                json={
                    "clientId": self.config["client_id"],
                    "clientSecret": self.config["client_secret"],
                },
            )
        resp.raise_for_status()
        self._access_token = resp.json()["token"]
        return self._access_token

    async def _headers(self) -> dict[str, str]:
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def create_card(self, payload: VirtualCardPayload) -> CardResult:
        customer = self.config["customer_hash_id"]
        wallet = self.config["wallet_hash_id"]
        headers = await self._headers()

        body = {
            "cardType": "VCN",
            "cardDesign": "virtual",
            "plasticId": "SINGLE_USE",
            "walletHashId": wallet,
            "cardExpiry": f"{payload.expiry_days}D",
            "limitAmount": str(payload.amount),
            "limitCurrency": payload.currency,
            "memo": f"{payload.vendor_name} - {payload.correlation_id}",
            "metadata": {
                "correlation_id": payload.correlation_id,
                "invoice_id": payload.invoice_id,
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base_url()}/customer/{customer}/cards",
                json=body,
                headers=headers,
            )

        if resp.status_code in (200, 201):
            data = resp.json()
            return CardResult(
                success=True,
                provider_card_id=data.get("cardHashId"),
                last_four=data.get("maskedCardNumber", "")[-4:],
                message="Card created via Nium",
                raw_response=data,
            )
        else:
            return CardResult(
                success=False,
                message=f"Nium error {resp.status_code}: {resp.text}",
            )

    async def get_card_details(self, provider_card_id: str) -> CardDetails:
        customer = self.config["customer_hash_id"]
        headers = await self._headers()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self._base_url()}/customer/{customer}/cards/{provider_card_id}/details",
                headers=headers,
            )

        resp.raise_for_status()
        data = resp.json()

        return CardDetails(
            card_number=data["unmaskedCardNumber"],
            exp_month=int(data["expiryMonth"]),
            exp_year=int(data["expiryYear"]),
            cvv=data["cvv"],
            last_four=data["unmaskedCardNumber"][-4:],
        )

    async def cancel_card(self, provider_card_id: str) -> bool:
        customer = self.config["customer_hash_id"]
        headers = await self._headers()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self._base_url()}/customer/{customer}/cards/{provider_card_id}/block",
                json={"reason": "CANCELLED_BY_AP_SYSTEM"},
                headers=headers,
            )
        return resp.status_code == 200

    async def get_card_status(self, provider_card_id: str) -> CardStatus:
        customer = self.config["customer_hash_id"]
        headers = await self._headers()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self._base_url()}/customer/{customer}/cards/{provider_card_id}",
                headers=headers,
            )

        if resp.status_code != 200:
            return CardStatus.created

        data = resp.json()
        status = data.get("cardStatus", "").upper()

        status_map = {
            "ACTIVE": CardStatus.active,
            "INACTIVE": CardStatus.created,
            "BLOCKED": CardStatus.cancelled,
            "EXPIRED": CardStatus.expired,
            "HOTLISTED": CardStatus.cancelled,
        }
        return status_map.get(status, CardStatus.active)

    async def test_connection(self) -> bool:
        try:
            await self._get_token()
            return True
        except Exception:
            return False
