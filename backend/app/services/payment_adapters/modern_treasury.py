"""Modern Treasury adapter — ACH / wire / RTP via the Modern Treasury REST API.

Modern Treasury sits between us and the customer's bank. We POST a Payment
Order with method (`ach`, `wire`, `rtp`, `check`), amount, originating
account (the customer's bank account, set up in MT once at onboarding),
and counterparty (the vendor's bank account). MT submits the rail and
sends webhooks as the payment moves through `pending` → `approved` →
`processing` → `completed` / `returned` / `failed`.

API docs: https://docs.moderntreasury.com/

Auth: HTTP Basic with `(organization_id, api_key)`.
Idempotency: send `Idempotency-Key` header — Modern Treasury de-dupes by it
for 24 hours. We send our `correlation_id` so a retry of the same logical
payment never double-pays.
Webhooks: HMAC-SHA256 over the request body, header `X-Signature`. Signing
key is per-tenant, configured in `org.settings.payments.webhook_secret`.

Local dev: set `sandbox: true` and use sandbox credentials. The base URL
is the same — Modern Treasury's sandbox is selected by the credential set,
not the URL.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

import httpx

from app.services.payment_adapters.base import (
    PaymentAdapter,
    PaymentPayload,
    PaymentResult,
    PaymentStatus,
    WebhookEvent,
    minor_units_to_decimal,
    to_minor_units,
)
from app.services.payment_adapters.dispatcher import register_payment_adapter

logger = logging.getLogger(__name__)

API_BASE = "https://app.moderntreasury.com/api"
TIMEOUT = 15.0

# Modern Treasury status → our status. Their status set is richer than ours
# (they distinguish `approved`, `sent`, `processing`); we collapse the
# in-flight ones into `submitted` and only call out terminal states.
_STATUS_MAP: dict[str, PaymentStatus] = {
    "needs_approval": PaymentStatus.submitted,
    "pending": PaymentStatus.submitted,
    "approved": PaymentStatus.submitted,
    "denied": PaymentStatus.failed,
    "sent": PaymentStatus.processing,
    "processing": PaymentStatus.processing,
    "completed": PaymentStatus.completed,
    "returned": PaymentStatus.failed,
    "failed": PaymentStatus.failed,
    "cancelled": PaymentStatus.cancelled,
}


@register_payment_adapter("modern_treasury")
class ModernTreasuryAdapter(PaymentAdapter):
    provider_name = "modern_treasury"
    supported_methods = ("ach", "wire", "rtp", "check")

    def __init__(self, config: dict):
        super().__init__(config)
        # Required: org_id, api_key, originating_account_id
        # Optional: webhook_secret (for parse_webhook), sandbox
        self.org_id: str = config.get("org_id", "")
        self.api_key: str = config.get("api_key", "")
        self.originating_account_id: str = config.get("originating_account_id", "")
        self.webhook_secret: str = config.get("webhook_secret", "")

    # ------------------------------------------------------------------
    # Internal HTTP

    def _auth(self) -> tuple[str, str]:
        return (self.org_id, self.api_key)

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def _post(self, path: str, body: dict, idempotency_key: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return await client.post(
                f"{API_BASE}{path}",
                json=body,
                auth=self._auth(),
                headers=self._headers(idempotency_key),
            )

    async def _get(self, path: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return await client.get(
                f"{API_BASE}{path}",
                auth=self._auth(),
                headers=self._headers(),
            )

    # ------------------------------------------------------------------
    # PaymentAdapter interface

    async def create_payment(self, payload: PaymentPayload) -> PaymentResult:
        # Both pre-flight refusals below follow the failure-reason convention the
        # other adapters use (`method '…' is not supported by …` / a bare
        # `<provider>_no_counterparty` code) rather than free prose. That is what
        # `services/payment_runs.classify_payment_failure` reads to decide a
        # failed payment is safe for `/retry-failed` to re-attempt: neither of
        # these ever reached Modern Treasury, so no order can exist there. Prose
        # here classifies as UNRECOGNISED — fail-closed, but it would leave every
        # such failure needing a manual reconcile on the flagship live rail.
        if payload.method not in self.supported_methods:
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=(
                    f"method '{payload.method}' is not supported by Modern Treasury "
                    f"(supports: {', '.join(self.supported_methods)})"
                ),
            )

        counterparty_id = (payload.vendor_bank or {}).get("counterparty_id")
        if not counterparty_id:
            # Without a counterparty we can't address the payment. Return a
            # structured failure so the orchestrator can record it on the
            # Payment row instead of throwing. (Set `vendor_bank.counterparty_id`
            # on the vendor record to resolve it.)
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="modern_treasury_no_counterparty",
            )

        # Modern Treasury expects amounts in the currency's lowest unit.
        # `to_minor_units` resolves the ISO-4217 exponent (2 for USD/EUR, 0 for
        # JPY/KRW, 3 for the Gulf dinars) rather than assuming cents — and it
        # is the exact inverse of what `parse_webhook` uses below, so a clean
        # settlement can never read as a mismatch. Decimal arithmetic
        # throughout prevents float drift on amounts like 19.99.
        amount_cents = to_minor_units(payload.amount, payload.currency)

        body = {
            "type": payload.method,
            "amount": amount_cents,
            "currency": payload.currency,
            "direction": "credit",
            "originating_account_id": self.originating_account_id,
            "receiving_account_id": counterparty_id,
            "description": payload.description or f"{payload.invoice_number}",
            "metadata": {
                "invoice_id": payload.invoice_id,
                "invoice_number": payload.invoice_number,
                "vendor_name": payload.vendor_name,
                **(payload.metadata or {}),
            },
        }

        try:
            resp = await self._post(
                "/payment_orders", body=body, idempotency_key=payload.correlation_id
            )
        except httpx.RequestError as exc:
            # Don't log the exc message — RequestError can carry the URL,
            # which on retried requests sometimes carries query-string
            # credentials the SDK appends. Class name is enough for triage;
            # audit dispatch captures the structured failure reason.
            logger.warning("Modern Treasury request error: %s", exc.__class__.__name__)
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason="Network error contacting Modern Treasury",
            )

        if resp.status_code >= 400:
            # Don't raise — return structured failure. The orchestrator
            # records it on the Payment row.
            detail = _extract_error(resp)
            logger.warning(
                "Modern Treasury rejected payment %s: %s %s",
                payload.correlation_id,
                resp.status_code,
                detail,
            )
            return PaymentResult(
                success=False,
                status=PaymentStatus.failed,
                failure_reason=f"Modern Treasury {resp.status_code}: {detail}",
                raw_response=_safe_json(resp),
            )

        data = resp.json()
        mt_status = data.get("status", "pending")
        return PaymentResult(
            success=True,
            status=_STATUS_MAP.get(mt_status, PaymentStatus.submitted),
            provider_payment_id=data.get("id"),
            # MT issues an actual ACH trace number once the payment posts;
            # we use the payment ID as the reference until the webhook
            # delivers the trace.
            reference=data.get("reference_number") or data.get("id"),
            raw_response=data,
        )

    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus:
        resp = await self._get(f"/payment_orders/{provider_payment_id}")
        if resp.status_code >= 400:
            logger.warning(
                "Modern Treasury status lookup failed for %s: %s",
                provider_payment_id,
                resp.status_code,
            )
            # Conservative: report as submitted (in-flight) so we don't
            # falsely flip a real-money payment to failed on a network blip.
            return PaymentStatus.submitted
        data = resp.json()
        return _STATUS_MAP.get(data.get("status", "pending"), PaymentStatus.submitted)

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        if not self.webhook_secret:
            logger.warning(
                "Modern Treasury webhook received but no webhook_secret configured — rejecting"
            )
            return None
        signature = headers.get("X-Signature") or headers.get("x-signature", "")
        expected = hmac.new(self.webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.warning("Modern Treasury webhook signature mismatch")
            return None

        try:
            payload = json.loads(body)
        except ValueError:
            return None

        # MT wraps webhook content under `data` and tags the resource type.
        # We only act on payment_order events — other event types (ledger
        # entries, expected_payments, etc.) we ignore.
        event_type = payload.get("event") or payload.get("event_type") or ""
        if not event_type.startswith("payment_order"):
            return None

        data = payload.get("data") or {}
        provider_id = data.get("id")
        mt_status = data.get("status")
        if not provider_id or mt_status not in _STATUS_MAP:
            return None

        # Modern Treasury includes the event id at the top level as `id`.
        # Fall back to a composite when the field is absent so dedup keeps
        # working on partial / older payloads.
        event_id = payload.get("id") or f"{provider_id}:{mt_status}"
        return WebhookEvent(
            provider_payment_id=provider_id,
            status=_STATUS_MAP[mt_status],
            event_id=str(event_id),
            reference=data.get("reference_number"),
            failure_reason=_failure_reason_from(data),
            occurred_at=payload.get("created_at") or datetime.now(UTC).isoformat(),
            # What MT says it actually settled. Its payment_order resource
            # carries `amount` in the lowest currency unit — the same scale
            # `create_payment` sends — so the inverse conversion is symmetric.
            currency=(data.get("currency") or None),
            amount=minor_units_to_decimal(data.get("amount"), data.get("currency")),
            raw=payload,
        )

    async def test_connection(self) -> bool:
        if not (self.org_id and self.api_key):
            return False
        try:
            # Listing payment orders with limit=1 is the cheapest auth check.
            resp = await self._get("/payment_orders?per_page=1")
        except httpx.RequestError:
            return False
        return resp.status_code < 400


# ---------------------------------------------------------------------------


def _safe_json(resp: httpx.Response) -> dict | None:
    try:
        return resp.json()
    except ValueError:
        return None


def _extract_error(resp: httpx.Response) -> str:
    body = _safe_json(resp) or {}
    if isinstance(body, dict):
        # MT errors look like {"errors": {"message": "..."}} or
        # {"message": "..."} depending on the endpoint.
        err = body.get("errors")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if body.get("message"):
            return str(body["message"])
    return resp.text[:200]


def _failure_reason_from(data: dict) -> str | None:
    # MT exposes return reasons under `return.reason_code` for ACH returns;
    # other rails surface failure detail under `decision_status_reason` or
    # `failure_reason`. We take the first that's set.
    for key in ("failure_reason", "decision_status_reason"):
        if data.get(key):
            return str(data[key])
    ret = data.get("return") or {}
    if isinstance(ret, dict) and (ret.get("reason_code") or ret.get("reason_description")):
        return f"{ret.get('reason_code', '')}: {ret.get('reason_description', '')}".strip(": ")
    return None
