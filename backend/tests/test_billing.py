"""Platform billing & metering — first slice.

Covers:
  * usage rollup math is Decimal-exact (pure-ish; uses the real control DB)
  * the mock billing adapter is the default + is deterministic
  * the stripe_billing adapter fails closed without a key
  * entitlement helpers allow/deny by plan
  * GET /api/billing/subscription returns the org's plan + status + usage
  * the /api/v1 public surface is plan-gated (402 without `public_api`, 200 with)

The adapter + entitlement-helper tests are pure (no DB) and always run. The
endpoint + rollup tests use the real-Postgres harness. Control-plane rows
(plans / subscriptions / extraction_usage) are NOT truncated between tests, so
every test that inserts them cleans up in a ``finally`` keyed to its org.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.models.billing import Plan, Subscription
from app.models.usage import ExtractionUsage
from app.models.virtual_card import CardRebate
from app.services.billing.entitlements import has_entitlement
from app.services.billing.usage_rollup import UsageRollup, rollup_usage
from app.services.billing_adapters import get_billing_adapter
from app.services.billing_adapters.base import CreateSubscriptionRequest
from app.services.billing_adapters.mock_adapter import MockBillingAdapter
from app.services.billing_adapters.stripe_billing import (
    BillingNotConfigured,
    StripeBillingAdapter,
)

PERIOD = "2026-06"


# ---------------------------------------------------------------------------
# Pure unit tests — no DB.
# ---------------------------------------------------------------------------


def test_get_billing_adapter_defaults_to_mock():
    adapter = get_billing_adapter()
    assert isinstance(adapter, MockBillingAdapter)
    assert adapter.provider_name == "mock"


def test_unknown_provider_falls_back_to_mock():
    assert isinstance(get_billing_adapter("does-not-exist"), MockBillingAdapter)


def test_stripe_adapter_selectable_by_name():
    assert isinstance(get_billing_adapter("stripe_billing"), StripeBillingAdapter)


@pytest.mark.asyncio
async def test_mock_adapter_is_deterministic():
    adapter = MockBillingAdapter()
    org_id = "org-123"
    req = CreateSubscriptionRequest(
        organization_id=org_id, plan_code="growth", monthly_price=Decimal("49.00"), trial_days=14
    )
    a = await adapter.create_subscription(req)
    b = await adapter.create_subscription(req)
    assert a.external_subscription_id == b.external_subscription_id == f"mock_sub_{org_id}"
    assert a.status == "trialing"  # trial_days > 0
    assert await adapter.test_connection() is True


def test_mock_adapter_parses_dev_webhook_envelope():
    adapter = MockBillingAdapter()
    body = (
        b'{"id": "evt_1", "type": "subscription.updated", '
        b'"subscription": "sub_1", "status": "active"}'
    )
    evt = adapter.parse_webhook({}, body)
    assert evt is not None
    assert evt.event_id == "evt_1"
    assert evt.event_type == "subscription.updated"
    assert evt.status == "active"
    # Malformed → None (route 204s silently).
    assert adapter.parse_webhook({}, b"not json") is None
    assert adapter.parse_webhook({}, b'{"type": "x"}') is None  # missing id


@pytest.mark.asyncio
async def test_stripe_adapter_fails_closed_without_key():
    adapter = StripeBillingAdapter({"stripe_api_key": "", "stripe_webhook_secret": ""})
    req = CreateSubscriptionRequest(
        organization_id="o", plan_code="growth", monthly_price=Decimal("49.00")
    )
    with pytest.raises(BillingNotConfigured):
        await adapter.create_subscription(req)
    # No key → test_connection reports unhealthy (never a false green).
    assert await adapter.test_connection() is False


def test_stripe_webhook_rejected_without_valid_hmac():
    # No secret configured → HMAC verification fails closed → None.
    adapter = StripeBillingAdapter({"stripe_api_key": "sk_test", "stripe_webhook_secret": ""})
    assert adapter.parse_webhook({"Stripe-Signature": "deadbeef"}, b'{"id":"x","type":"y"}') is None


def test_stripe_webhook_accepts_valid_hmac():
    import hashlib
    import hmac

    secret = "whsec_test"
    body = (
        b'{"id": "evt_9", "type": "customer.subscription.updated", '
        b'"data": {"object": {"id": "sub_9", "status": "past_due"}}}'
    )
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    adapter = StripeBillingAdapter({"stripe_api_key": "sk", "stripe_webhook_secret": secret})
    evt = adapter.parse_webhook({"Stripe-Signature": sig}, body)
    assert evt is not None
    assert evt.event_id == "evt_9"
    assert evt.external_subscription_id == "sub_9"
    assert evt.status == "past_due"  # mapped


def test_has_entitlement_truthiness():
    ents = {"public_api": True, "max_seats": 25, "disabled_feature": False}
    assert has_entitlement(ents, "public_api") is True
    assert has_entitlement(ents, "max_seats") is True  # truthy non-bool
    assert has_entitlement(ents, "disabled_feature") is False
    assert has_entitlement(ents, "missing") is False
    assert has_entitlement({}, "public_api") is False  # no plan → no entitlement


def test_usage_rollup_as_meters_serialises_decimal_as_string():
    r = UsageRollup(
        organization_id="o",
        period=PERIOD,
        extractions=3,
        extractions_platform=2,
        card_rebate_total=Decimal("12.34"),
    )
    meters = r.as_meters()
    assert meters["extractions"] == "3"
    assert meters["extractions_platform"] == "2"
    assert meters["card_rebate_total"] == "12.34"
    assert all(isinstance(v, str) for v in meters.values())


# ---------------------------------------------------------------------------
# Real-DB: usage rollup math + endpoint + plan gating.
# ---------------------------------------------------------------------------


async def _cleanup_billing(realdb, org_id):
    """Remove the plan/subscription (control plane) + usage (tenant DB) rows a
    test created — neither is truncated by the fixture. All billing tests act as
    tenant "a"."""
    async with realdb.control_sessionmaker()() as s:
        await s.execute(delete(Subscription).where(Subscription.organization_id == org_id))
        # Drop any test plans (by the per-test codes we use below).
        await s.execute(delete(Plan).where(Plan.code.like("test_%")))
        await s.commit()
    # extraction_usage / card_rebates are per-tenant tables — clean them in the
    # tenant DB, not the control plane.
    async with realdb.sessionmaker("a")() as s:
        await s.execute(delete(ExtractionUsage).where(ExtractionUsage.organization_id == org_id))
        await s.execute(delete(CardRebate).where(CardRebate.organization_id == org_id))
        await s.commit()


async def _seed_plan(realdb, *, code, entitlements, price="49.00"):
    plan_id = uuid.uuid4()
    async with realdb.control_sessionmaker()() as s:
        s.add(
            Plan(
                id=plan_id,
                code=code,
                name=code.title(),
                monthly_price=Decimal(price),
                currency="USD",
                entitlements=entitlements,
                trial_days=14,
            )
        )
        await s.commit()
    return plan_id


async def _seed_subscription(realdb, *, org_id, plan_id, status="active"):
    async with realdb.control_sessionmaker()() as s:
        s.add(
            Subscription(
                id=uuid.uuid4(),
                organization_id=org_id,
                plan_id=plan_id,
                status=status,
                current_period_start=datetime(2026, 6, 1, tzinfo=UTC),
                current_period_end=datetime(2026, 6, 30, tzinfo=UTC),
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_rollup_usage_is_decimal_exact(realdb):
    org_id = realdb.info("a").org_id
    try:
        # extraction_usage is a per-tenant table — seed + read it on the tenant DB.
        async with realdb.sessionmaker("a")() as s:
            for i in range(3):
                s.add(
                    ExtractionUsage(
                        id=uuid.uuid4(),
                        invoice_id=uuid.uuid4(),
                        provider="mock",
                        program_type="platform" if i < 2 else "byok",
                        period=PERIOD,
                        organization_id=org_id,
                    )
                )
            await s.commit()

        async with realdb.sessionmaker("a")() as s:
            rollup = await rollup_usage(s, organization_id=org_id, period=PERIOD)
        assert rollup.extractions == 3
        assert rollup.extractions_platform == 2
        assert isinstance(rollup.card_rebate_total, Decimal)
        assert rollup.card_rebate_total == Decimal("0.00")  # no rebates
        # A period with no activity returns a zero-filled rollup, never None.
        async with realdb.sessionmaker("a")() as s:
            empty = await rollup_usage(s, organization_id=org_id, period="1999-01")
        assert empty.extractions == 0
    finally:
        await _cleanup_billing(realdb, org_id)


@pytest.mark.asyncio
async def test_subscription_endpoint_returns_plan_status_and_usage(realdb):
    org_id = realdb.info("a").org_id
    try:
        plan_id = await _seed_plan(
            realdb, code="test_growth", entitlements={"public_api": True, "max_seats": 25}
        )
        await _seed_subscription(realdb, org_id=org_id, plan_id=plan_id, status="active")
        # Seed one extraction so usage-to-date is non-zero for the current period
        # (extraction_usage is per-tenant — seed it in the tenant DB).
        period = datetime.now(UTC).strftime("%Y-%m")
        async with realdb.sessionmaker("a")() as s:
            s.add(
                ExtractionUsage(
                    id=uuid.uuid4(),
                    invoice_id=uuid.uuid4(),
                    provider="mock",
                    program_type="platform",
                    period=period,
                    organization_id=org_id,
                )
            )
            await s.commit()

        async with realdb.client(key="a", role="admin") as c:
            resp = await c.get("/api/billing/subscription")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["plan"]["code"] == "test_growth"
        assert body["plan"]["monthly_price"] == "49.00"  # exact string, not float
        assert body["plan"]["entitlements"]["public_api"] is True
        assert body["subscription"]["status"] == "active"
        assert body["subscription"]["externally_managed"] is False
        assert body["provider"] == "mock"
        assert body["usage"]["extractions"] == "1"
    finally:
        await _cleanup_billing(realdb, org_id)


@pytest.mark.asyncio
async def test_subscription_endpoint_admin_or_cfo_only(realdb):
    org_id = realdb.info("a").org_id
    try:
        async with realdb.client(key="a", role="ap_clerk") as c:
            resp = await c.get("/api/billing/subscription")
        assert resp.status_code == 403
        async with realdb.client(key="a", role="cfo") as c:
            resp = await c.get("/api/billing/subscription")
        assert resp.status_code == 200
    finally:
        await _cleanup_billing(realdb, org_id)


@pytest.mark.asyncio
async def test_no_subscription_yields_null_plan(realdb):
    org_id = realdb.info("a").org_id
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.get("/api/billing/subscription")
        assert resp.status_code == 200
        body = resp.json()
        assert body["plan"] is None
        assert body["subscription"] is None
    finally:
        await _cleanup_billing(realdb, org_id)


@pytest.mark.asyncio
async def test_public_api_is_plan_gated(realdb):
    """The /api/v1 surface 402s when the plan lacks `public_api`, 200s when it
    includes it. Demonstrates require_api_entitlement composing with the scope."""
    from app.services.api_keys import KEY_BRAND  # noqa: F401

    org_id = realdb.info("a").org_id
    try:
        # Mint a read key for tenant A.
        async with realdb.client(key="a", role="admin") as c:
            mint = await c.post("/api/api-keys", json={"name": "billing-test"})
        assert mint.status_code == 201, mint.text
        api_key = mint.json()["key"]

        def _key_client():
            cc = realdb.client(key="a", role=None)
            cc.headers.pop("X-Tenant-Slug", None)
            cc.headers["X-API-Key"] = api_key
            return cc

        # No plan at all → no entitlement → 402.
        async with _key_client() as c:
            resp = await c.get("/api/v1/invoices")
        assert resp.status_code == 402, resp.text

        # Plan WITHOUT public_api → still 402.
        plan_no = await _seed_plan(realdb, code="test_free", entitlements={"public_api": False})
        await _seed_subscription(realdb, org_id=org_id, plan_id=plan_no, status="active")
        async with _key_client() as c:
            resp = await c.get("/api/v1/invoices")
        assert resp.status_code == 402

        # Swap to a plan WITH public_api → 200.
        async with realdb.control_sessionmaker()() as s:
            await s.execute(delete(Subscription).where(Subscription.organization_id == org_id))
            await s.commit()
        plan_yes = await _seed_plan(
            realdb, code="test_scale", entitlements={"public_api": True}
        )
        await _seed_subscription(realdb, org_id=org_id, plan_id=plan_yes, status="active")
        async with _key_client() as c:
            resp = await c.get("/api/v1/invoices")
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup_billing(realdb, org_id)
