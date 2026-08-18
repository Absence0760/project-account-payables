"""Regression: the billing webhook releases its Redis dedup claim on failure.

`billing_webhook` claims a Redis dedup slot (`is_event_already_processed`)
BEFORE running `apply_billing_event`. If `apply_billing_event` (or its
control-plane commit) raises, the claim must be released — otherwise the
provider's retry of the same event id is silently deduped away for the full TTL
and the subscription-lifecycle transition (e.g. → past_due) is lost forever.

This mirrors the claim/release discipline `api/cards.py::card_webhook` already
has. The core assertion is behavioural: a first delivery that raises leaves the
event id UN-claimed, so a redelivery of the same event id reprocesses instead of
being swallowed.

The suite's autouse ``_autouse_fake_redis`` fixture stubs the dedup ledger with
an in-memory fake; requesting it by name hands back the SAME fake instance the
handler's ``webhook_security.get_redis`` resolves to, so the test can inspect the
exact key the handler claims/releases (no real-Redis cross-loop hazard).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from app.config import settings
from app.services.webhook_security import DEDUP_PREFIX

_WEBHOOK_SECRET = "whsec_test_billing_release"


def _sign(body: bytes, secret: str = _WEBHOOK_SECRET, *, timestamp: str | None = None) -> str:
    # `t` defaults to NOW: the adapter enforces Stripe's replay-tolerance
    # window, so a fixed epoch would reject every request in this file.
    if timestamp is None:
        timestamp = str(int(time.time()))
    signed_payload = b"%s.%s" % (timestamp.encode(), body)
    digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _stripe_event(*, event_id: str, sub_id: str, raw_status: str) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": "customer.subscription.updated",
            "data": {"object": {"id": sub_id, "status": raw_status}},
        }
    ).encode()


def _webhook_client(realdb):
    c = realdb.client(key="a", role=None)
    c.headers.pop("X-Tenant-Slug", None)
    return c


def _dedup_key(provider: str, event_id: str) -> str:
    # Must match the handler's `is_event_already_processed(f"billing:{provider}", …)`
    # → `f"{DEDUP_PREFIX}{provider}:{event_id}"` exactly.
    return f"{DEDUP_PREFIX}billing:{provider}:{event_id}"


@pytest.fixture
def _enable_stripe_webhook(monkeypatch):
    monkeypatch.setattr(settings, "billing_webhook_enabled", True)
    monkeypatch.setattr(settings, "billing_provider", "stripe_billing")
    monkeypatch.setattr(settings, "billing_stripe_webhook_secret", _WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "billing_stripe_api_key", "sk_test")


class _FakeResult:
    applied = True
    reason = None


@pytest.mark.asyncio
async def test_apply_failure_releases_claim_so_redelivery_reprocesses(
    realdb, _enable_stripe_webhook, _autouse_fake_redis, monkeypatch
):
    """A raising `apply_billing_event` must NOT leave the event id deduped.

    Force the FIRST apply to raise; assert (1) the request surfaces the error
    (the provider gets a non-2xx and will retry) and (2) the dedup claim is gone
    afterward, so a redelivery of the SAME event id calls apply again rather than
    being silently short-circuited. Under the bug the claim persists and apply is
    called exactly once — this test's call-count assertion fails.
    """
    import app.api.billing_webhook as bw

    fake = _autouse_fake_redis  # the exact fake the handler dedups against
    event_id = "evt_release_1"
    key = _dedup_key("stripe_billing", event_id)

    calls = {"n": 0}

    async def _fake_apply(control_db, *, event):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate the commit (or its lifecycle work) blowing up AFTER the
            # dedup slot was already claimed in step 4 of the handler.
            raise RuntimeError("boom: commit failed")
        return _FakeResult()

    monkeypatch.setattr(bw, "apply_billing_event", _fake_apply)

    body = _stripe_event(event_id=event_id, sub_id="sub_release_1", raw_status="past_due")
    headers = {"Stripe-Signature": _sign(body)}

    # First delivery: apply raises → the handler re-raises (5xx to the provider
    # so it retries) AFTER releasing the claim.
    async with _webhook_client(realdb) as c:
        with pytest.raises(RuntimeError, match="boom"):
            await c.post("/api/billing/webhook/stripe_billing", content=body, headers=headers)

    assert calls["n"] == 1
    # The claim was released — the event id is free to be reprocessed.
    assert await fake.get(key) is None

    # Redelivery of the SAME event id reprocesses (apply called again), instead
    # of being deduped away for the TTL window.
    async with _webhook_client(realdb) as c:
        resp = await c.post("/api/billing/webhook/stripe_billing", content=body, headers=headers)
    assert resp.status_code == 204
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_apply_success_keeps_claim_so_true_duplicate_is_deduped(
    realdb, _enable_stripe_webhook, _autouse_fake_redis, monkeypatch
):
    """The release is failure-only: on success the claim stands, so a genuine
    provider redelivery within the TTL is still deduped (the effect ran once)."""
    import app.api.billing_webhook as bw

    fake = _autouse_fake_redis
    event_id = "evt_release_ok"
    key = _dedup_key("stripe_billing", event_id)

    calls = {"n": 0}

    async def _fake_apply(control_db, *, event):
        calls["n"] += 1
        return _FakeResult()

    monkeypatch.setattr(bw, "apply_billing_event", _fake_apply)

    body = _stripe_event(event_id=event_id, sub_id="sub_release_ok", raw_status="past_due")
    headers = {"Stripe-Signature": _sign(body)}

    async with _webhook_client(realdb) as c:
        r1 = await c.post("/api/billing/webhook/stripe_billing", content=body, headers=headers)
        r2 = await c.post("/api/billing/webhook/stripe_billing", content=body, headers=headers)
    assert r1.status_code == r2.status_code == 204
    # Claim persisted through success → second delivery deduped, apply ran once.
    assert calls["n"] == 1
    assert await fake.get(key) is not None
