"""Lithic card adapter — default provider for US/UK/EU markets.

Lithic offers interchange sharing from day one with simple API integration.
Docs: https://docs.lithic.com

Idempotency: `POST /v1/cards` honours the `Idempotency-Key` header (one of the
two endpoints Lithic supports it on today), and the key **must be a valid
UUID**. We send the caller-supplied `VirtualCardPayload.idempotency_key` — a
deterministic UUID5 minted by `card_issuance.build_card_idempotency_key` — so a
retry after a client-side timeout returns the card Lithic already created
instead of provisioning a second live one.
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
from app.services.payment_adapters.base import to_minor_units

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

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"api-key {self.config['api_key']}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def create_card(self, payload: VirtualCardPayload) -> CardResult:
        body = {
            "type": "SINGLE_USE",
            # Minor units, resolved through the ONE ISO-4217 exponent table this
            # codebase has (`payment_adapters.base`). This was a flat
            # `int(amount * 100)` while the read half — `api/cards.
            # _normalize_charge_amount`, which de-scales a Lithic webhook amount
            # — had already been migrated to `minor_units_to_decimal`. That
            # asymmetry is the state the base module explicitly warns is worse
            # than the original symmetric bug: a ¥500,000 card went out as a
            # ¥50,000,000 authorization ceiling (exponent 0, so no scaling
            # applies), 100x the payable and spendable by the vendor, while the
            # charge that came back was de-scaled correctly and `card_settlement_
            # block` — which only ever compares our own `amount_limit` — could
            # not see it. 5.000 KWD is the mirror image: 500 fils instead of
            # 5,000, a 10x under-limit that declines a legitimate charge.
            # `to_minor_units` also rounds ROUND_HALF_UP rather than truncating.
            "spend_limit": to_minor_units(payload.amount, payload.currency),
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
                # Idempotency-Key is what makes a retry safe: Lithic replays the
                # original response for a repeated key instead of minting a
                # second live card (30-day key retention).
                headers=self._headers(payload.idempotency_key),
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
        if resp.status_code == 200:
            # A 200 may echo the resulting state; a card already CLOSED/TERMINATED
            # is still a success, not a failed cancel.
            try:
                state = str(resp.json().get("state", "")).upper()
            except Exception:  # noqa: BLE001
                state = ""
            if state in ("", "CLOSED", "TERMINATED"):
                return True
            return False
        # Idempotent cancel: a card already closed/terminated at the provider is
        # SUCCESS, not a failure. This cleanly resolves the retry case where a
        # first cancel closed the card at the provider but the DB write failed
        # and AP retries — the second attempt should confirm, not error.
        #   - 404: the card no longer exists at the provider → nothing to close
        #   - 409: state conflict (already CLOSED) → nothing to close
        if resp.status_code in (404, 409):
            return True
        # Any other error: confirm against the live state — an already-CLOSED
        # card counts as cancelled; anything else (or an unreachable status
        # check) stays a non-confirmed False, preserving the fail-safe direction.
        return await self.get_card_status(provider_card_id) == CardStatus.cancelled

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
