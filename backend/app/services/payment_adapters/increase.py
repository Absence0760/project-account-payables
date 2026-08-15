"""Increase adapter — ACH + wire + check via Increase's REST API.

Increase exposes one resource per rail:
  - POST /ach_transfers          — same-day or standard ACH
  - POST /wire_transfers         — domestic wire (FedWire)
  - POST /check_transfers        — Increase prints + mails the check

Money debits from a configured account_id; the destination is an
existing external_account_id (set up in Increase via the dashboard
or our vendor-onboarding flow and stored on
`vendor.bank_details.counterparty_id`).

API docs: https://increase.com/documentation

Auth: Bearer token.
Idempotency: every POST takes `Idempotency-Key` header (alphanumeric,
1–256 chars). We send our `correlation_id`.
Webhooks: HMAC-SHA256 over the raw body, header
`Increase-Webhook-Signature: t=<ts>,v1=<hex>` (same structure as
Stripe). The signing secret is per-endpoint, set in
`Organization.settings.payments.webhook_secret`.

Sandbox: separate `production` vs `sandbox` flag in config — base
URL differs (`api.increase.com` vs `sandbox.increase.com`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from decimal import ROUND_HALF_UP, Decimal

import httpx

from app.services.payment_adapters.base import (
    CorridorQuote,
    PaymentAdapter,
    PaymentPayload,
    PaymentResult,
    PaymentStatus,
    WebhookEvent,
    minor_units_to_decimal,
)
from app.services.payment_adapters.dispatcher import register_payment_adapter

logger = logging.getLogger(__name__)

PROD_BASE = "https://api.increase.com"
SANDBOX_BASE = "https://sandbox.increase.com"
TIMEOUT = 15.0
SIGNATURE_TOLERANCE_SECONDS = 5 * 60

# Increase uses different status sets per resource. We normalise.
_STATUS_MAP: dict[str, PaymentStatus] = {
    "pending_approval": PaymentStatus.submitted,
    "pending_submission": PaymentStatus.submitted,
    "pending_reviewing": PaymentStatus.submitted,
    "submitted": PaymentStatus.processing,
    "complete": PaymentStatus.completed,
    "completed": PaymentStatus.completed,
    "canceled": PaymentStatus.cancelled,
    "returned": PaymentStatus.failed,
    "rejected": PaymentStatus.failed,
    "requires_attention": PaymentStatus.failed,
}

# Method → Increase endpoint.
_ENDPOINT: dict[str, str] = {
    "ach": "/ach_transfers",
    "wire": "/wire_transfers",
    "check": "/check_transfers",
}


@register_payment_adapter("increase")
class IncreaseAdapter(PaymentAdapter):
    provider_name = "increase"
    supported_methods = ("ach", "wire", "check")

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key: str = config.get("api_key", "")
        self.account_id: str = config.get("account_id", "")  # debit source
        self.webhook_secret: str = config.get("webhook_secret", "")
        self.sandbox: bool = bool(config.get("sandbox", True))

    def _base(self) -> str:
        return SANDBOX_BASE if self.sandbox else PROD_BASE

    # ------------------------------------------------------------------
    # create_payment
    # ------------------------------------------------------------------

    async def create_payment(self, payload: PaymentPayload) -> PaymentResult:
        if not self.api_key:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="increase_not_configured",
            )
        if payload.method not in self.supported_methods:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=(
                    f"method '{payload.method}' is not supported by Increase "
                    f"(supports: {', '.join(self.supported_methods)})"
                ),
            )

        external_account = (payload.vendor_bank or {}).get("counterparty_id")
        if not external_account:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="increase_no_external_account",
            )

        # ROUND_HALF_UP for the minor-unit conversion (not Decimal's default
        # banker's rounding) — consistent with international_payments and the
        # rest of the money path so a .x5 cent never rounds down.
        amount_minor = int(
            (payload.amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        body = {
            "account_id": self.account_id,
            "amount": amount_minor,
            "statement_descriptor": (payload.invoice_number or "AP")[:10],
            "external_account_id": external_account,
            "require_approval": False,
        }
        # Wire transfers have a `message_to_recipient` field; checks
        # carry a memo. Both render on the rail.
        if payload.method == "wire":
            body["message_to_recipient"] = (
                payload.description or payload.invoice_number or "Payment"
            )[:35]
        elif payload.method == "check":
            body["recipient_name"] = payload.vendor_name[:50]
            body["amount"] = amount_minor
            # Check transfers want a structured mailing address; we
            # don't model that yet — fall back to provider failure.

        headers = {
            "Authorization": f"Bearer {self.api_key}",
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
                failure_reason=f"increase_transport_error:{exc.__class__.__name__}",
            )

        if response.status_code >= 400:
            try:
                err = response.json() or {}
            except ValueError:
                err = {}
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=f"increase_api_error:{err.get('type') or response.status_code}",
            )

        data = response.json()
        return PaymentResult(
            success=True,
            status=_STATUS_MAP.get(data.get("status", ""), PaymentStatus.submitted),
            provider_payment_id=data.get("id", ""),
            reference=data.get("transaction_id") or data.get("id"),
            raw_response=data,
        )

    # ------------------------------------------------------------------

    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus:
        if not self.api_key:
            return PaymentStatus.failed
        # The transfer ID prefix tells us which resource to GET:
        # `ach_transfer_...`, `wire_transfer_...`, `check_transfer_...`.
        resource = "ach_transfers"
        if provider_payment_id.startswith("wire_transfer_"):
            resource = "wire_transfers"
        elif provider_payment_id.startswith("check_transfer_"):
            resource = "check_transfers"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{self._base()}/{resource}/{provider_payment_id}",
                headers=headers,
            )
        if response.status_code >= 400:
            return PaymentStatus.failed
        return _STATUS_MAP.get(response.json().get("status", ""), PaymentStatus.submitted)

    # ------------------------------------------------------------------

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        if not self.webhook_secret:
            return None
        sig_header = headers.get("Increase-Webhook-Signature") or headers.get(
            "increase-webhook-signature"
        )
        if not sig_header:
            return None
        parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
        ts = parts.get("t")
        provided = parts.get("v1")
        if not ts or not provided:
            return None
        try:
            ts_int = int(ts)
        except ValueError:
            return None
        if abs(time.time() - ts_int) > SIGNATURE_TOLERANCE_SECONDS:
            return None

        signed_payload = f"{ts}.".encode() + body
        expected = hmac.new(
            self.webhook_secret.encode(), signed_payload, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, provided):
            return None

        try:
            event = json.loads(body.decode())
        except (UnicodeDecodeError, ValueError):
            return None

        # Increase fires `*.transfer.updated` events; the object
        # carries our status.
        obj = event.get("associated_object") or {}
        provider_payment_id = obj.get("id") or event.get("associated_object_id")
        if not provider_payment_id:
            return None
        status = _STATUS_MAP.get(obj.get("status", ""), PaymentStatus.submitted)
        event_id = event.get("id") or f"{provider_payment_id}:{status.value}"
        return WebhookEvent(
            provider_payment_id=provider_payment_id,
            status=status,
            event_id=str(event_id),
            reference=obj.get("transaction_id"),
            failure_reason=(obj.get("decline") or {}).get("reason"),
            occurred_at=event.get("created_at"),
            # What Increase says it actually settled. Transfer amounts come
            # back in minor units, the same scale `create_payment` sends;
            # `currency` is present on wire transfers and absent on ACH
            # (USD-only), and an absent currency is a wildcard to the verifier.
            amount=minor_units_to_decimal(obj.get("amount")),
            currency=(obj.get("currency") or None),
            raw=event,
        )

    async def test_connection(self) -> bool:
        if not self.api_key:
            return False
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{self._base()}/accounts",
                headers=headers,
                params={"limit": 1},
            )
        return response.status_code < 400

    async def quote_payment(self, payload: PaymentPayload) -> CorridorQuote:
        if payload.method not in self.supported_methods:
            return CorridorQuote(
                provider=self.provider_name,
                method=payload.method,
                available=False,
                unavailable_reason=f"method '{payload.method}' not supported by increase",
            )
        # Increase pricing (published, USD): ACH $0.50 flat;
        # wire $0.50 + $1 outbound; check $1.00 print + postage.
        flat = {
            "ach": Decimal("0.50"),
            "wire": Decimal("1.50"),
            "check": Decimal("1.00"),
        }[payload.method]
        override = (self.config.get("flat_fees") or {}).get(payload.method)
        if override is not None:
            flat = Decimal(str(override))
        eta = {"ach": 1, "wire": 0, "check": 5}[payload.method]
        return CorridorQuote(
            provider=self.provider_name,
            method=payload.method,
            available=True,
            flat_fee=flat,
            pct_fee=Decimal("0"),
            eta_business_days=eta,
        )
