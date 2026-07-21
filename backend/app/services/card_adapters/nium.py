"""Nium card adapter — provider for non-US/UK/EU markets (40+ countries).

Nium offers global card issuance with interchange sharing.
Docs: https://docs.nium.com

Idempotency: Nium supports it on every POST API, and the key travels in the
`x-request-id` header (NOT `Idempotency-Key` — that is Lithic's convention).
We send the caller-supplied `VirtualCardPayload.idempotency_key` on card
creation so a retry after a client-side timeout resolves to the card Nium
already issued rather than issuing a second live one. Nium purges keys after
24 hours, so the guarantee covers the retry window that matters (a re-issue
weeks later legitimately gets a fresh key — see
`card_issuance.build_card_idempotency_key`).
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

    async def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        token = await self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            # Nium's idempotency channel is `x-request-id` (docs: Developers →
            # Nium API → Idempotency). Same key on a retry → same card back.
            headers["x-request-id"] = idempotency_key
        return headers

    async def create_card(self, payload: VirtualCardPayload) -> CardResult:
        customer = self.config["customer_hash_id"]
        wallet = self.config["wallet_hash_id"]
        headers = await self._headers(payload.idempotency_key)

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
        if resp.status_code == 200:
            return True
        # Idempotent cancel: a card already blocked/terminated at the provider is
        # SUCCESS, not a failure. This cleanly resolves the retry case where a
        # first block succeeded at the provider but the DB write failed and AP
        # retries — the second attempt should confirm, not error.
        #   - 404: the card no longer exists at the provider → nothing to block
        #   - 409: state conflict (already BLOCKED/HOTLISTED) → nothing to block
        if resp.status_code in (404, 409):
            return True
        # Any other error: confirm against the live state — an already
        # BLOCKED/HOTLISTED card maps to CardStatus.cancelled; anything else (or
        # an unreachable status check) stays a non-confirmed False, preserving
        # the fail-safe direction.
        return await self.get_card_status(provider_card_id) == CardStatus.cancelled

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
