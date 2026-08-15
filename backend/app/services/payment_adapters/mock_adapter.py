"""Mock payment adapter — for local dev and the test suite.

Behaves like a processor that immediately confirms every payment. Generates
a deterministic-shape reference (`MOCK-{method}-{8-char hex}`) so tests can
assert on the format without asserting on a specific UUID. Always reports
success.

This is the default adapter when no provider is configured (see dispatcher).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.services.payment_adapters.base import (
    BalanceResult,
    CorridorQuote,
    PaymentAdapter,
    PaymentPayload,
    PaymentResult,
    PaymentStatus,
    SettlementReport,
    WebhookEvent,
    parse_amount,
)
from app.services.payment_adapters.dispatcher import register_payment_adapter


@register_payment_adapter("mock")
class MockPaymentAdapter(PaymentAdapter):
    provider_name = "mock"
    supported_methods = (
        "ach",
        "wire",
        "check",
        "rtp",
        "virtual_card",
        "sepa",
        "international_wire",
        "international_ach",
    )

    # Default fee schedule baked in so the quote optimizer has
    # something deterministic to rank. Tests can override via the
    # adapter config: `{"provider": "mock", "fees": {"ach": "0.0050"}}`.
    _DEFAULT_FEES: dict[str, Decimal] = {
        "ach": Decimal("0.0010"),
        "wire": Decimal("0.0050"),
        "rtp": Decimal("0.0020"),
        "check": Decimal("0"),
        "sepa": Decimal("0.0005"),
        "international_ach": Decimal("0.0080"),
        "international_wire": Decimal("0.0250"),
        "virtual_card": Decimal("0"),
    }
    _DEFAULT_ETA_DAYS: dict[str, int] = {
        "ach": 2,
        "wire": 0,
        "rtp": 0,
        "check": 5,
        "sepa": 1,
        "international_ach": 3,
        "international_wire": 1,
        "virtual_card": 0,
    }

    async def quote_payment(self, payload: PaymentPayload) -> CorridorQuote:
        """Return a deterministic quote so the optimizer has data.

        Honors `self.config["fees"]` / `self.config["eta_days"]` so a
        test can inject "this mock is the cheapest" / "this mock is
        the fastest" without subclassing. Methods not in
        `supported_methods` come back unavailable."""
        if payload.method not in self.supported_methods:
            return CorridorQuote(
                provider=self.provider_name,
                method=payload.method,
                available=False,
                unavailable_reason=f"method '{payload.method}' not supported by mock",
            )
        fees_override = self.config.get("fees") or {}
        eta_override = self.config.get("eta_days") or {}
        # Per-config overrides win; otherwise use the baked defaults.
        default_pct = self._DEFAULT_FEES.get(payload.method, Decimal("0"))
        pct = fees_override.get(payload.method, default_pct)
        default_eta = self._DEFAULT_ETA_DAYS.get(payload.method, 0)
        eta = eta_override.get(payload.method, default_eta)
        return CorridorQuote(
            provider=self.provider_name,
            method=payload.method,
            available=True,
            flat_fee=Decimal("0"),
            pct_fee=Decimal(str(pct)),
            eta_business_days=int(eta),
            fx_rate=payload.fx_rate,
        )

    async def create_payment(self, payload: PaymentPayload) -> PaymentResult:
        # Mock always settles immediately. Real processors take seconds to
        # days; the webhook handler is what completes them in production.
        provider_id = f"mock_pmt_{uuid.uuid4().hex[:12]}"
        reference = f"MOCK-{payload.method.upper()}-{uuid.uuid4().hex[:8].upper()}"
        return PaymentResult(
            success=True,
            status=PaymentStatus.completed,
            provider_payment_id=provider_id,
            reference=reference,
            raw_response={"mock": True, "correlation_id": payload.correlation_id},
        )

    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus:
        # Mock has no real lifecycle — anything we minted is "completed."
        return PaymentStatus.completed

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        # Mock doesn't send webhooks; this exists so test fixtures can call
        # it directly to simulate a status transition.
        try:
            import json

            payload = json.loads(body or b"{}")
        except ValueError:
            return None
        provider_payment_id = payload.get("provider_payment_id")
        status = payload.get("status")
        if not provider_payment_id or status not in PaymentStatus.__members__:
            return None
        event_id = payload.get("event_id") or f"{provider_payment_id}:{status}"
        return WebhookEvent(
            provider_payment_id=provider_payment_id,
            status=PaymentStatus(status),
            event_id=str(event_id),
            reference=payload.get("reference"),
            failure_reason=payload.get("failure_reason"),
            occurred_at=datetime.now(UTC).isoformat(),
            # MAJOR units, like the real decimal-string rails. Optional so a
            # fixture that omits it keeps reading `unverified`; supplying it is
            # how a test exercises the settlement-mismatch branch with no
            # processor account (guard rail 7 — every external behaviour has a
            # local equivalent). The public webhook ROUTE rejects
            # `provider == "mock"` outright, so this is reached by calling the
            # adapter directly, which is the only way mock webhooks ever
            # arrive anyway.
            amount=parse_amount(payload.get("amount")),
            currency=(payload.get("currency") or None),
            raw=payload,
        )

    # Deterministic funding-account balance so the cash-position dashboard can
    # auto-seed an opening balance in local dev with no real bank credential.
    _DEFAULT_BALANCE = Decimal("250000.00")

    async def get_balance(self) -> BalanceResult:
        """Return a deterministic balance for local dev / tests.

        Honors `self.config["balance"]` / `self.config["balance_currency"]` so a
        test can inject a specific figure (or `available: false` to simulate a
        processor without the capability) without subclassing."""
        if self.config.get("balance_available") is False:
            return BalanceResult(available=False, unavailable_reason="disabled_in_config")
        raw = self.config.get("balance", self._DEFAULT_BALANCE)
        return BalanceResult(
            available=True,
            amount=Decimal(str(raw)),
            currency=self.config.get("balance_currency", "USD"),
            account_ref="mock-operating",
        )

    async def fetch_settlement(self, provider_payment_id: str) -> SettlementReport:
        """Deterministic settled figure for local dev / tests.

        Mirrors `get_balance`'s config hooks so a test can inject a specific
        amount — including one that DIFFERS from the authorization, which is
        how the reconciler's under-settlement path gets exercised locally —
        or `settlement_available: false` to simulate a processor without the
        capability, without stubbing HTTP or subclassing.
        """
        if self.config.get("settlement_available") is False:
            return SettlementReport(available=False, unavailable_reason="disabled_in_config")
        raw = self.config.get("settled_amount")
        if raw is None:
            return SettlementReport(available=False, unavailable_reason="not_configured")
        return SettlementReport(
            available=True,
            amount=Decimal(str(raw)),
            currency=self.config.get("settled_currency", "USD"),
        )

    async def test_connection(self) -> bool:
        return True

    async def void_payment(self, provider_payment_id: str) -> bool:
        # Mock accepts every void — gives test fixtures a working
        # `voided_upstream` audit outcome without stubbing any HTTP.
        return True
