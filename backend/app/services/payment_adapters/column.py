"""Column adapter — ACH + book transfers + wires via Column BAAS API.

Column is a fintech-friendly bank that exposes deposit accounts +
ACH/wire origination via REST. Money debits from a bank_account_id;
the destination is either:
  - another Column bank_account_id (book transfer — instant + free)
  - an external counterparty (ACH or wire)

API docs: https://column.com/docs/api

Auth: HTTP Basic with `<api_key>:` (empty password).
Idempotency: `Idempotency-Key` header.
Webhooks: HMAC-SHA256 over body, header `Column-Signature`.
Sandbox: `https://api.sandbox.column.com` vs production base.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from decimal import ROUND_HALF_UP, Decimal

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

PROD_BASE = "https://api.column.com"
SANDBOX_BASE = "https://api.sandbox.column.com"
TIMEOUT = 15.0

_STATUS_MAP: dict[str, PaymentStatus] = {
    "initiated": PaymentStatus.submitted,
    "pending_review": PaymentStatus.submitted,
    "manual_review": PaymentStatus.submitted,
    "approved": PaymentStatus.processing,
    "submitted": PaymentStatus.processing,
    "settled": PaymentStatus.completed,
    "completed": PaymentStatus.completed,
    "returned": PaymentStatus.failed,
    "rejected": PaymentStatus.failed,
    "canceled": PaymentStatus.cancelled,
}

_ENDPOINT: dict[str, str] = {
    "ach": "/transfers/ach",
    "wire": "/transfers/wire",
    "book": "/transfers/book",
}


@register_payment_adapter("column")
class ColumnAdapter(PaymentAdapter):
    provider_name = "column"
    # `book` isn't a standard rail name in our codebase; we map it
    # internally to `ach` for the purpose of `payment.method`, but
    # adapters call it out as book in the API request.
    supported_methods = ("ach", "wire")

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key: str = config.get("api_key", "")
        self.bank_account_id: str = config.get("bank_account_id", "")  # debit source
        self.webhook_secret: str = config.get("webhook_secret", "")
        self.sandbox: bool = bool(config.get("sandbox", True))

    def _base(self) -> str:
        return SANDBOX_BASE if self.sandbox else PROD_BASE

    def _auth_header(self) -> dict[str, str]:
        token = base64.b64encode(f"{self.api_key}:".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    async def create_payment(self, payload: PaymentPayload) -> PaymentResult:
        if not self.api_key:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="column_not_configured",
            )
        if payload.method not in self.supported_methods:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=(
                    f"method '{payload.method}' is not supported by Column "
                    f"(supports: {', '.join(self.supported_methods)})"
                ),
            )

        counterparty_id = (payload.vendor_bank or {}).get("counterparty_id")
        if not counterparty_id:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="column_no_counterparty",
            )

        # ROUND_HALF_UP for the minor-unit conversion (not Decimal's default
        # banker's rounding) — consistent with international_payments and the
        # rest of the money path so a .x5 cent never rounds down.
        amount_minor = int(
            (payload.amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        # Column uses `same_day` flag for SDA; default to standard.
        body = {
            "amount": amount_minor,
            "currency_code": payload.currency.upper(),
            "bank_account_id": self.bank_account_id,
            "counterparty_id": counterparty_id,
            "description": (payload.description or payload.invoice_number or "")[:80],
            "idempotency_key": payload.correlation_id,
        }
        if payload.method == "ach":
            body["type"] = "credit"
            body["ach_sec_code"] = "ccd"  # corporate credit/debit
            body["same_day"] = False

        headers = {
            **self._auth_header(),
            "Idempotency-Key": payload.correlation_id,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(
                    f"{self._base()}{_ENDPOINT[payload.method]}",
                    json=body,
                    headers=headers,
                )
        except httpx.RequestError as exc:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=f"column_transport_error:{exc.__class__.__name__}",
            )

        if response.status_code >= 400:
            try:
                err = response.json() or {}
            except ValueError:
                err = {}
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=f"column_api_error:{err.get('code') or response.status_code}",
            )

        data = response.json()
        return PaymentResult(
            success=True,
            status=_STATUS_MAP.get(data.get("status", ""), PaymentStatus.submitted),
            provider_payment_id=data.get("id", ""),
            reference=data.get("trace_number") or data.get("id"),
            raw_response=data,
        )

    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus:
        if not self.api_key:
            return PaymentStatus.failed
        # The transfer prefix selects the right resource — Column
        # uses `acht_...` for ACH, `wire_...` for wires.
        resource = "ach"
        if provider_payment_id.startswith("wire_"):
            resource = "wire"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{self._base()}/transfers/{resource}/{provider_payment_id}",
                headers=self._auth_header(),
            )
        if response.status_code >= 400:
            return PaymentStatus.failed
        return _STATUS_MAP.get(response.json().get("status", ""), PaymentStatus.submitted)

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        if not self.webhook_secret:
            return None
        provided = headers.get("Column-Signature") or headers.get("column-signature")
        if not provided:
            return None
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            return None
        try:
            event = json.loads(body.decode())
        except (UnicodeDecodeError, ValueError):
            return None
        # Column events: { type: "transfer.completed", data: {...} }
        if not str(event.get("type", "")).startswith("transfer."):
            return None
        obj = event.get("data") or {}
        provider_payment_id = obj.get("id")
        if not provider_payment_id:
            return None
        event_id = event.get("id") or f"{provider_payment_id}:{obj.get('status', '')}"
        return WebhookEvent(
            provider_payment_id=provider_payment_id,
            status=_STATUS_MAP.get(obj.get("status", ""), PaymentStatus.submitted),
            event_id=str(event_id),
            reference=obj.get("trace_number"),
            failure_reason=obj.get("return_reason"),
            occurred_at=event.get("created_at"),
            raw=event,
        )

    async def test_connection(self) -> bool:
        if not self.api_key:
            return False
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{self._base()}/bank-accounts",
                headers=self._auth_header(),
                params={"limit": 1},
            )
        return response.status_code < 400

    async def quote_payment(self, payload: PaymentPayload) -> CorridorQuote:
        if payload.method not in self.supported_methods:
            return CorridorQuote(
                provider=self.provider_name,
                method=payload.method,
                available=False,
                unavailable_reason=f"method '{payload.method}' not supported by column",
            )
        # Column pricing: ACH $0.10 flat; wire $5.00 flat.
        flat = Decimal("0.10") if payload.method == "ach" else Decimal("5.00")
        override = (self.config.get("flat_fees") or {}).get(payload.method)
        if override is not None:
            flat = Decimal(str(override))
        eta = 1 if payload.method == "ach" else 0
        return CorridorQuote(
            provider=self.provider_name,
            method=payload.method,
            available=True,
            flat_fee=flat,
            pct_fee=Decimal("0"),
            eta_business_days=eta,
        )
