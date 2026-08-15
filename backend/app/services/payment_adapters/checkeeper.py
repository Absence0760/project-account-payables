"""Checkeeper adapter — check printing + mailing via Checkeeper REST API.

Checkeeper prints and mails physical checks on the org's behalf. We
POST a check object with payer + payee details + memo + amount;
Checkeeper prints and ships it. Tracking webhooks fire on print
(check leaves the printer) and on delivery (USPS scan).

API docs: https://checkeeper.com/api/

Auth: Bearer token in `Authorization` header.
Idempotency: NOT supported natively by Checkeeper — a physical check is
an irreversible negotiable instrument, so we de-dupe client-side via a
`correlation_id`-keyed Redis `SET NX` claimed right before issuing (see
`create_payment`). A second call with the same correlation_id finds the
slot taken and is suppressed instead of printing a second check;
correlation_id is fresh per payment ATTEMPT, so a legitimate re-pay
(new attempt) gets its own slot. We also stamp `metadata.correlation_id`
on the check so a later reconciliation can match webhook → payment row.
Webhooks: HMAC-SHA256 over body, header `X-Checkeeper-Signature`.
Sandbox: `api.sandbox.checkeeper.com` vs `api.checkeeper.com`.

This adapter is **check-only**. Other methods return unavailable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from decimal import Decimal

import httpx

from app.services.payment_adapters.base import (
    CorridorQuote,
    PaymentAdapter,
    PaymentPayload,
    PaymentResult,
    PaymentStatus,
    WebhookEvent,
    parse_amount,
)
from app.services.payment_adapters.dispatcher import register_payment_adapter

logger = logging.getLogger(__name__)

PROD_BASE = "https://api.checkeeper.com"
SANDBOX_BASE = "https://api.sandbox.checkeeper.com"
TIMEOUT = 15.0
# How long a correlation_id's idempotency slot is held. 48h comfortably
# covers the check print + USPS delivery + reconciliation window, so a
# late retry of the same attempt can't slip a second physical check out.
CHECK_IDEMPOTENCY_TTL_SECONDS = 48 * 60 * 60

_STATUS_MAP: dict[str, PaymentStatus] = {
    "queued": PaymentStatus.submitted,
    "printing": PaymentStatus.submitted,
    "printed": PaymentStatus.processing,
    "shipped": PaymentStatus.processing,
    "delivered": PaymentStatus.completed,
    "cleared": PaymentStatus.completed,
    "voided": PaymentStatus.cancelled,
    "failed": PaymentStatus.failed,
    "returned": PaymentStatus.failed,
}


@register_payment_adapter("checkeeper")
class CheckeeperAdapter(PaymentAdapter):
    provider_name = "checkeeper"
    supported_methods = ("check",)

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key: str = config.get("api_key", "")
        # The payer side — the org's bank account that Checkeeper
        # prints checks against. Configured in Checkeeper dashboard.
        self.bank_account_id: str = config.get("bank_account_id", "")
        self.webhook_secret: str = config.get("webhook_secret", "")
        self.sandbox: bool = bool(config.get("sandbox", True))

    def _base(self) -> str:
        return SANDBOX_BASE if self.sandbox else PROD_BASE

    async def create_payment(self, payload: PaymentPayload) -> PaymentResult:
        if not self.api_key:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="checkeeper_not_configured",
            )
        if payload.method != "check":
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=(
                    f"method '{payload.method}' is not supported by Checkeeper (check only)"
                ),
            )

        # Vendor mailing address rides on the vendor_bank dict under
        # the `mailing_address` key (street, city, state, postal,
        # country). Without it we can't print a check.
        bank = payload.vendor_bank or {}
        addr = bank.get("mailing_address") or {}
        required = ("street", "city", "state", "postal")
        if not all(addr.get(k) for k in required):
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="checkeeper_missing_mailing_address",
            )

        body = {
            "bank_account_id": self.bank_account_id,
            "payee": {
                "name": payload.vendor_name,
                "address": {
                    "line1": addr["street"],
                    "city": addr["city"],
                    "state": addr["state"],
                    "postal_code": addr["postal"],
                    "country": addr.get("country", "US"),
                },
            },
            "amount": str(payload.amount.quantize(Decimal("0.01"))),
            "currency": payload.currency.upper(),
            "memo": (payload.description or payload.invoice_number or "")[:35],
            "metadata": {
                "invoice_id": payload.invoice_id,
                "correlation_id": payload.correlation_id,
            },
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Client-side idempotency gate. Checkeeper has no native idempotency
        # key, so this Redis SET-NX is the ONLY thing standing between a
        # retried create_payment (same correlation_id) and a second physical
        # check in the mail. Claim the slot right before issuing; a taken slot
        # means a prior attempt already issued (or is issuing) this check.
        from app.redis import get_redis

        idem_key = f"checkeeper:payment:{payload.correlation_id}"
        try:
            r = await get_redis()
            was_set = await r.set(idem_key, "1", nx=True, ex=CHECK_IDEMPOTENCY_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            # Without Redis we can't guarantee idempotency — fail CLOSED rather
            # than risk a duplicate irreversible check. The operator retries
            # once Redis is healthy. Log the class only (never the body).
            logger.warning(
                "checkeeper idempotency gate unavailable (%s); refusing to issue "
                "a check without a dedup guard",
                exc.__class__.__name__,
            )
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="checkeeper_idempotency_unavailable",
            )
        if not was_set:
            # A prior attempt with this exact correlation_id already claimed the
            # slot. Suppress the duplicate — do NOT POST a second check.
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="checkeeper_duplicate_suppressed",
            )

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(
                    f"{self._base()}/checks",
                    json=body,
                    headers=headers,
                )
        except httpx.RequestError as exc:
            # Ambiguous: Checkeeper may have received + printed the check before
            # the transport failed. Leave the idempotency slot CLAIMED so an
            # automatic retry can't print a second check; the operator
            # reconciles via the tracking webhook / audit log.
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=f"checkeeper_transport_error:{exc.__class__.__name__}",
            )

        if response.status_code >= 400:
            # A clean API rejection means NO check was printed — release the
            # slot so a legitimate retry of this correlation_id can proceed.
            try:
                await r.delete(idem_key)
            except Exception:  # noqa: BLE001
                pass
            try:
                err = response.json() or {}
            except ValueError:
                err = {}
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=f"checkeeper_api_error:{err.get('code') or response.status_code}",
            )

        data = response.json()
        return PaymentResult(
            success=True,
            status=_STATUS_MAP.get(data.get("status", ""), PaymentStatus.submitted),
            provider_payment_id=data.get("id", ""),
            reference=data.get("check_number") or data.get("id"),
            raw_response=data,
        )

    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus:
        if not self.api_key:
            return PaymentStatus.failed
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{self._base()}/checks/{provider_payment_id}",
                headers=headers,
            )
        if response.status_code >= 400:
            return PaymentStatus.failed
        return _STATUS_MAP.get(response.json().get("status", ""), PaymentStatus.submitted)

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        if not self.webhook_secret:
            return None
        provided = headers.get("X-Checkeeper-Signature") or headers.get("x-checkeeper-signature")
        if not provided:
            return None
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            return None
        try:
            event = json.loads(body.decode())
        except (UnicodeDecodeError, ValueError):
            return None
        obj = event.get("check") or {}
        provider_payment_id = obj.get("id")
        if not provider_payment_id:
            return None
        event_id = event.get("id") or f"{provider_payment_id}:{obj.get('status', '')}"
        return WebhookEvent(
            provider_payment_id=provider_payment_id,
            status=_STATUS_MAP.get(obj.get("status", ""), PaymentStatus.submitted),
            event_id=str(event_id),
            reference=obj.get("check_number"),
            failure_reason=obj.get("failure_code"),
            occurred_at=event.get("created_at"),
            # The face value Checkeeper printed. Unlike the card/ACH rails it
            # exchanges MAJOR-unit decimal strings (see `create_payment`), so
            # this parses rather than de-scales. A cheque whose cleared amount
            # differs from the issued one is the classic altered-cheque case —
            # the same call `positive_pay.classify_presented_items` makes from
            # the bank's presented-items file, made here at issue time.
            amount=parse_amount(obj.get("amount")),
            currency=(obj.get("currency") or None),
            raw=event,
        )

    async def test_connection(self) -> bool:
        if not self.api_key:
            return False
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{self._base()}/bank-accounts",
                headers=headers,
            )
        return response.status_code < 400

    async def quote_payment(self, payload: PaymentPayload) -> CorridorQuote:
        if payload.method != "check":
            return CorridorQuote(
                provider=self.provider_name,
                method=payload.method,
                available=False,
                unavailable_reason="method not supported by checkeeper (check only)",
            )
        # Checkeeper pricing: $1.49 print + USPS first-class postage
        # ($0.66). Expedited mail is an add-on we don't model.
        flat = Decimal(str((self.config.get("flat_fees") or {}).get("check", "2.15")))
        return CorridorQuote(
            provider=self.provider_name,
            method="check",
            available=True,
            flat_fee=flat,
            pct_fee=Decimal("0"),
            eta_business_days=5,
        )
