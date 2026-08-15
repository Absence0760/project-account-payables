"""Dwolla adapter — ACH-only specialist via Dwolla's REST API.

Dwolla focuses on ACH origination + funding-source management. Money
moves between Funding Sources; each Funding Source is either:
  - the customer's "Balance" wallet at Dwolla, OR
  - a verified external bank account (funding-source URL)

API docs: https://developers.dwolla.com/

Auth: OAuth client-credentials grant. Tokens last 1h; we fetch on
demand and cache for 50 min (a short safety margin). Same pattern
as the Nium card adapter.

Idempotency: `Idempotency-Key` header on POST /transfers.
Webhooks: HMAC-SHA256 over body, header `X-Request-Signature-SHA-256`.
Sandbox: `api-sandbox.dwolla.com` vs `api.dwolla.com`.

This adapter is **ACH-only**. Dwolla does not provide wire or check
products. Other rails return unavailable from `quote_payment`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal

import httpx

from app.services.payment_adapters.base import (
    CorridorQuote,
    PaymentAdapter,
    PaymentPayload,
    PaymentResult,
    PaymentStatus,
    WebhookEvent,
)
from app.services.payment_adapters.dispatcher import register_payment_adapter

logger = logging.getLogger(__name__)

PROD_BASE = "https://api.dwolla.com"
SANDBOX_BASE = "https://api-sandbox.dwolla.com"
TIMEOUT = 15.0

_STATUS_MAP: dict[str, PaymentStatus] = {
    "pending": PaymentStatus.submitted,
    "processed": PaymentStatus.completed,
    "failed": PaymentStatus.failed,
    "cancelled": PaymentStatus.cancelled,
    "reclaimed": PaymentStatus.failed,
}


@register_payment_adapter("dwolla")
class DwollaAdapter(PaymentAdapter):
    provider_name = "dwolla"
    supported_methods = ("ach",)

    def __init__(self, config: dict):
        super().__init__(config)
        self.client_id: str = config.get("client_id", "")
        self.client_secret: str = config.get("client_secret", "")
        # Source funding-source URL (the org's Dwolla balance or
        # verified bank). The Dwolla URL form is the API contract;
        # we don't try to short-circuit to just the ID.
        self.source_funding_source: str = config.get("source_funding_source", "")
        self.webhook_secret: str = config.get("webhook_secret", "")
        self.sandbox: bool = bool(config.get("sandbox", True))
        # Cached access token + expiry epoch.
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        # Single-flight lock so concurrent payments don't double-fetch.
        self._token_lock = asyncio.Lock()

    def _base(self) -> str:
        return SANDBOX_BASE if self.sandbox else PROD_BASE

    async def _get_token(self) -> str | None:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            # Re-check after acquiring lock — another coroutine may
            # have minted a token while we waited.
            if self._token and time.time() < self._token_expires_at:
                return self._token
            if not self.client_id or not self.client_secret:
                return None
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(
                    f"{self._base()}/token",
                    data={"grant_type": "client_credentials"},
                    auth=(self.client_id, self.client_secret),
                )
            if response.status_code >= 400:
                return None
            body = response.json()
            self._token = body.get("access_token")
            # Dwolla returns `expires_in` in seconds. We renew 10 min
            # before actual expiry to avoid mid-request 401s.
            self._token_expires_at = time.time() + int(body.get("expires_in", 3600)) - 600
            return self._token

    async def create_payment(self, payload: PaymentPayload) -> PaymentResult:
        token = await self._get_token()
        if not token:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="dwolla_not_configured",
            )
        if payload.method != "ach":
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=f"method '{payload.method}' is not supported by Dwolla (ach only)",
            )
        destination_url = (payload.vendor_bank or {}).get("counterparty_id")
        if not destination_url:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="dwolla_no_destination_funding_source",
            )

        # Dwolla amounts are decimal strings (NOT minor units). Two
        # decimal places, no thousands separators.
        amount_str = f"{payload.amount.quantize(Decimal('0.01'))}"
        body = {
            "_links": {
                "source": {"href": self.source_funding_source},
                "destination": {"href": destination_url},
            },
            "amount": {"currency": payload.currency.upper(), "value": amount_str},
            "metadata": {
                "invoice_id": payload.invoice_id,
                "correlation_id": payload.correlation_id,
            },
            "clearing": {"destination": "next-available"},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.dwollav1.hal+json",
            "Content-Type": "application/vnd.dwollav1.hal+json",
            "Idempotency-Key": payload.correlation_id,
        }

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(
                    f"{self._base()}/transfers",
                    json=body,
                    headers=headers,
                )
        except httpx.RequestError as exc:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=f"dwolla_transport_error:{exc.__class__.__name__}",
            )

        if response.status_code >= 400:
            try:
                err = response.json() or {}
            except ValueError:
                err = {}
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=f"dwolla_api_error:{err.get('code') or response.status_code}",
            )

        # Dwolla returns 201 with `Location` header pointing at the
        # new transfer; we extract the ID from the URL trailing seg.
        location = response.headers.get("Location") or response.headers.get("location") or ""
        provider_id = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
        return PaymentResult(
            success=True,
            status=PaymentStatus.submitted,  # Dwolla starts in pending.
            provider_payment_id=provider_id,
            reference=provider_id,
            raw_response={"location": location},
        )

    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus:
        token = await self._get_token()
        if not token:
            return PaymentStatus.failed
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.dwollav1.hal+json",
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{self._base()}/transfers/{provider_payment_id}",
                headers=headers,
            )
        if response.status_code >= 400:
            return PaymentStatus.failed
        return _STATUS_MAP.get(response.json().get("status", ""), PaymentStatus.submitted)

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        if not self.webhook_secret:
            return None
        provided = headers.get("X-Request-Signature-SHA-256") or headers.get(
            "x-request-signature-sha-256"
        )
        if not provided:
            return None
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            return None
        try:
            event = json.loads(body.decode())
        except (UnicodeDecodeError, ValueError):
            return None
        # Dwolla's event topics: `transfer_completed`, `transfer_failed`,
        # `transfer_returned`, `transfer_cancelled`. The transfer ID
        # lives in `resourceId`.
        topic = event.get("topic", "")
        if not topic.startswith("transfer_"):
            return None
        provider_payment_id = event.get("resourceId")
        if not provider_payment_id:
            return None
        status = {
            "transfer_completed": PaymentStatus.completed,
            "transfer_failed": PaymentStatus.failed,
            "transfer_returned": PaymentStatus.failed,
            "transfer_cancelled": PaymentStatus.cancelled,
        }.get(topic, PaymentStatus.submitted)
        event_id = event.get("id") or f"{provider_payment_id}:{topic}"
        # No settled `amount` / `currency`: a Dwolla event body is a bare
        # `{id, topic, resourceId, _links}` envelope — the transfer's amount
        # is only available by following `_links.resource`, which this
        # signature-verification path deliberately does not do (it is
        # synchronous and must not make a network call). The settlement
        # verifier reads that as `unverified` — an honest blind spot recorded
        # on the audit row — rather than inventing a discrepancy. Closing it
        # means an async re-fetch of the transfer; the downstream net remains
        # bank reconciliation.
        return WebhookEvent(
            provider_payment_id=provider_payment_id,
            status=status,
            event_id=str(event_id),
            reference=provider_payment_id,
            failure_reason=event.get("failureCode"),
            occurred_at=event.get("created"),
            raw=event,
        )

    async def test_connection(self) -> bool:
        token = await self._get_token()
        return token is not None

    async def quote_payment(self, payload: PaymentPayload) -> CorridorQuote:
        if payload.method != "ach":
            return CorridorQuote(
                provider=self.provider_name,
                method=payload.method,
                available=False,
                unavailable_reason="method not supported by dwolla (ach only)",
            )
        # Dwolla pricing: $0.25 per ACH transfer on the standard plan;
        # SDA add-on $1.00. We quote the standard rate.
        flat = Decimal(str((self.config.get("flat_fees") or {}).get("ach", "0.25")))
        return CorridorQuote(
            provider=self.provider_name,
            method="ach",
            available=True,
            flat_fee=flat,
            pct_fee=Decimal("0"),
            eta_business_days=3,
        )
