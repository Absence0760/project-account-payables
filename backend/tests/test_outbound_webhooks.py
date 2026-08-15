"""Outbound Developer-API webhooks — subscription, signing, delivery, retries.

Covers:
  * pure signing primitives (secret shape, HMAC matches webhook_security style)
  * subscription CRUD is admin-gated; create returns the signing secret once,
    list/get never leak the full secret
  * a delivery is signed + POSTed; 2xx → delivered, non-2xx → retry w/ backoff,
    exhaustion → dead-letter
  * emit_event enqueues one delivery per matching active subscription and dedupes
    a re-fired event by (subscription, event_id)
  * tenant isolation — an admin can only see / mutate their own org's
    subscriptions + deliveries
  * redelivery re-enqueues a failed/dead delivery and re-attempts

The CRUD + emit + isolation tests need the real-Postgres harness (control-plane
tables). The signing + delivery-classification tests are pure-ish (delivery uses
an in-memory control DB row + a stubbed HTTP transport).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.models.webhook import (
    DELIVERY_DEAD,
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    EVENT_INVOICE_APPROVED,
    EVENT_PAYMENT_SETTLED,
    WebhookDelivery,
    WebhookSubscription,
)
from app.services.webhooks import delivery as delivery_mod
from app.services.webhooks.signing import (
    SECRET_BRAND,
    SECRET_PREFIX_LEN,
    generate_signing_secret,
    sign_payload,
)


@pytest.fixture(autouse=True)
def _allow_test_targets(monkeypatch):
    """These tests use non-resolvable `example.test` targets and stub the HTTP
    layer — flip the SSRF-guard escape hatch (the committed local-dev default)
    so the target-address check doesn't reject them. The guard itself is
    covered, with the flag OFF, in test_webhook_url_guard.py."""
    from app.config import settings

    monkeypatch.setattr(settings, "webhooks_allow_private_targets", True)


# ---------------------------------------------------------------------------
# Pure signing-primitive tests (no DB).
# ---------------------------------------------------------------------------


def test_generate_signing_secret_shape():
    secret, prefix = generate_signing_secret()
    assert secret.startswith(f"{SECRET_BRAND}_")
    assert prefix == secret[:SECRET_PREFIX_LEN]
    assert prefix != secret  # prefix is not the whole secret


def test_generate_signing_secret_unique():
    secrets = {generate_signing_secret()[0] for _ in range(50)}
    assert len(secrets) == 50


def test_sign_payload_matches_inbound_verify_primitive():
    """The outbound signature must be exactly what webhook_security verifies."""
    from app.services.webhook_security import verify_hmac_sha256

    secret = "whsec_test"
    body = b'{"hello":"world"}'
    sig = sign_payload(secret, body)
    assert len(sig) == 64  # sha256 hex
    # The receiver re-derives the identical primitive and it verifies.
    assert verify_hmac_sha256(secret, body, sig) is True
    # A tampered body / wrong secret fails.
    assert verify_hmac_sha256(secret, body + b"x", sig) is False
    assert verify_hmac_sha256("whsec_other", body, sig) is False


def test_next_backoff_is_exponential():
    b1 = delivery_mod._next_backoff(1)
    b2 = delivery_mod._next_backoff(2)
    b3 = delivery_mod._next_backoff(3)
    assert b1 == timedelta(seconds=delivery_mod.BACKOFF_BASE_SECONDS)
    assert b2 == timedelta(seconds=delivery_mod.BACKOFF_BASE_SECONDS * 2)
    assert b3 == timedelta(seconds=delivery_mod.BACKOFF_BASE_SECONDS * 4)


# ---------------------------------------------------------------------------
# Delivery classification — needs a control-plane sub + delivery row, but stubs
# the HTTP transport so no real network call happens.
# ---------------------------------------------------------------------------


def _make_sub_and_delivery(org_id, *, target="https://example.test/hook"):
    secret, prefix = generate_signing_secret()
    sub = WebhookSubscription(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="t",
        target_url=target,
        event_types=[EVENT_INVOICE_APPROVED],
        signing_secret=secret,
        secret_prefix=prefix,
        active=True,
    )
    delivery = WebhookDelivery(
        id=uuid.uuid4(),
        subscription_id=sub.id,
        organization_id=org_id,
        event_id=f"{EVENT_INVOICE_APPROVED}:{uuid.uuid4()}",
        event_type=EVENT_INVOICE_APPROVED,
        payload={"id": "x", "type": EVENT_INVOICE_APPROVED, "data": {}},
        status=DELIVERY_PENDING,
        attempt_count=0,
        next_attempt_at=datetime.now(UTC),
    )
    return sub, delivery


async def _persist(control_mk, *rows):
    # Insert subscriptions before deliveries so the FK (delivery →
    # subscription) is always satisfied, regardless of UoW flush ordering.
    subs = [r for r in rows if isinstance(r, WebhookSubscription)]
    others = [r for r in rows if not isinstance(r, WebhookSubscription)]
    async with control_mk() as s:
        for r in subs:
            s.add(r)
        if subs:
            await s.flush()
        for r in others:
            s.add(r)
        await s.commit()


async def _cleanup(control_mk, *sub_ids):
    from sqlalchemy import delete

    async with control_mk() as s:
        for sid in sub_ids:
            await s.execute(delete(WebhookDelivery).where(WebhookDelivery.subscription_id == sid))
            await s.execute(delete(WebhookSubscription).where(WebhookSubscription.id == sid))
        await s.commit()


@pytest.mark.asyncio
async def test_delivery_success_marks_delivered(realdb, monkeypatch):
    org_id = realdb.info("a").org_id
    control_mk = realdb.control_sessionmaker()
    sub, dlv = _make_sub_and_delivery(org_id)
    await _persist(control_mk, sub, dlv)

    captured: dict = {}

    async def fake_post(target_url, body, signature, delivery, previous_signature=None):
        captured["url"] = target_url
        captured["body"] = body
        captured["sig"] = signature
        captured["prev_sig"] = previous_signature
        captured["event_id_header"] = delivery.event_id
        return 200

    monkeypatch.setattr(delivery_mod, "_post", fake_post)

    try:
        async with control_mk() as s:
            row = await s.get(WebhookDelivery, dlv.id)
            await delivery_mod.process_delivery(s, row)
            assert row.status == DELIVERY_DELIVERED
            assert row.response_code == 200
            assert row.attempt_count == 1
            assert row.next_attempt_at is None
        # The signed body is exactly what the receiver would verify.
        assert captured["sig"] == sign_payload(sub.signing_secret, captured["body"])
        # No rotation in flight -> no secondary header at all.
        assert captured["prev_sig"] is None
    finally:
        await _cleanup(control_mk, sub.id)


@pytest.mark.asyncio
async def test_delivery_failure_schedules_retry_then_dead_letters(realdb, monkeypatch):
    org_id = realdb.info("a").org_id
    control_mk = realdb.control_sessionmaker()
    sub, dlv = _make_sub_and_delivery(org_id)
    await _persist(control_mk, sub, dlv)

    async def fake_post(*a, **k):
        return 500  # always fails

    monkeypatch.setattr(delivery_mod, "_post", fake_post)

    try:
        # Drive attempts up to the limit; each failed attempt (until the last)
        # stays `failed` with a scheduled next_attempt_at.
        for n in range(1, delivery_mod.MAX_ATTEMPTS):
            async with control_mk() as s:
                row = await s.get(WebhookDelivery, dlv.id)
                await delivery_mod.process_delivery(s, row)
                assert row.attempt_count == n
                assert row.status == DELIVERY_FAILED
                assert row.next_attempt_at is not None
                # Reset the clock so the next process_delivery is "due".
                row.next_attempt_at = datetime.now(UTC)
                await s.commit()
        # Final attempt exhausts retries → dead-letter.
        async with control_mk() as s:
            row = await s.get(WebhookDelivery, dlv.id)
            await delivery_mod.process_delivery(s, row)
            assert row.status == DELIVERY_DEAD
            assert row.attempt_count == delivery_mod.MAX_ATTEMPTS
            assert row.next_attempt_at is None
    finally:
        await _cleanup(control_mk, sub.id)


@pytest.mark.asyncio
async def test_transport_error_is_a_failed_attempt(realdb, monkeypatch):
    org_id = realdb.info("a").org_id
    control_mk = realdb.control_sessionmaker()
    sub, dlv = _make_sub_and_delivery(org_id)
    await _persist(control_mk, sub, dlv)

    async def fake_post(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(delivery_mod, "_post", fake_post)

    try:
        async with control_mk() as s:
            row = await s.get(WebhookDelivery, dlv.id)
            await delivery_mod.process_delivery(s, row)
            # Transport error → failed attempt, no response code.
            assert row.status == DELIVERY_FAILED
            assert row.response_code is None
            assert row.attempt_count == 1
    finally:
        await _cleanup(control_mk, sub.id)


# ---------------------------------------------------------------------------
# emit_event — enqueue + dedupe (gated on the master switch).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_enqueues_one_per_matching_active_sub_and_dedupes(realdb, monkeypatch):
    from app.config import settings
    from app.services.webhooks import dispatch as dispatch_mod

    org_id = realdb.info("a").org_id
    control_mk = realdb.control_sessionmaker()

    # Two subscriptions: one subscribed to invoice.approved + active, one
    # subscribed only to payment.settled (should not match), one inactive.
    s_match, _ = _make_sub_and_delivery(org_id)
    s_match.event_types = [EVENT_INVOICE_APPROVED]
    s_other, _ = _make_sub_and_delivery(org_id)
    s_other.event_types = [EVENT_PAYMENT_SETTLED]
    s_inactive, _ = _make_sub_and_delivery(org_id)
    s_inactive.event_types = [EVENT_INVOICE_APPROVED]
    s_inactive.active = False
    await _persist(control_mk, s_match, s_other, s_inactive)

    monkeypatch.setattr(settings, "webhooks_enabled", True)
    # Suppress the fire-and-forget immediate attempt so the test inspects only
    # the enqueue/dedupe behaviour.
    monkeypatch.setattr(dispatch_mod, "_spawn_immediate_attempt", lambda did: None)

    from sqlalchemy import select

    try:
        await dispatch_mod.emit_event(
            organization_id=org_id,
            event_type=EVENT_INVOICE_APPROVED,
            event_key="inv-1",
            data={"invoice_id": "inv-1"},
        )
        async with control_mk() as s:
            rows = (
                (
                    await s.execute(
                        select(WebhookDelivery).where(WebhookDelivery.organization_id == org_id)
                    )
                )
                .scalars()
                .all()
            )
        # Exactly one delivery, to the matching active subscription only.
        assert len(rows) == 1
        assert rows[0].subscription_id == s_match.id
        assert rows[0].event_id == f"{EVENT_INVOICE_APPROVED}:inv-1"

        # Re-firing the SAME event does NOT create a second delivery (dedupe on
        # (subscription, event_id)).
        await dispatch_mod.emit_event(
            organization_id=org_id,
            event_type=EVENT_INVOICE_APPROVED,
            event_key="inv-1",
            data={"invoice_id": "inv-1"},
        )
        async with control_mk() as s:
            rows2 = (
                (
                    await s.execute(
                        select(WebhookDelivery).where(WebhookDelivery.organization_id == org_id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows2) == 1
    finally:
        await _cleanup(control_mk, s_match.id, s_other.id, s_inactive.id)


@pytest.mark.asyncio
async def test_emit_is_noop_when_disabled(realdb, monkeypatch):
    from sqlalchemy import select

    from app.config import settings
    from app.services.webhooks import dispatch as dispatch_mod

    org_id = realdb.info("a").org_id
    control_mk = realdb.control_sessionmaker()
    s_match, _ = _make_sub_and_delivery(org_id)
    s_match.event_types = [EVENT_INVOICE_APPROVED]
    await _persist(control_mk, s_match)

    monkeypatch.setattr(settings, "webhooks_enabled", False)
    try:
        await dispatch_mod.emit_event(
            organization_id=org_id,
            event_type=EVENT_INVOICE_APPROVED,
            event_key="inv-x",
            data={},
        )
        async with control_mk() as s:
            rows = (
                (
                    await s.execute(
                        select(WebhookDelivery).where(WebhookDelivery.organization_id == org_id)
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []
    finally:
        await _cleanup(control_mk, s_match.id)


# ---------------------------------------------------------------------------
# Management API — admin-gated CRUD, secret-once, isolation, redelivery.
# ---------------------------------------------------------------------------


async def _create_sub(c, **overrides):
    body = {
        "name": "ci-hook",
        # A public IP literal — passes the SSRF guard deterministically without a
        # DNS lookup (issue #171).
        "target_url": "https://93.184.216.34/hook",
        "event_types": [EVENT_INVOICE_APPROVED],
    }
    body.update(overrides)
    return await c.post("/api/webhooks", json=body)


@pytest.mark.asyncio
async def test_create_returns_secret_once_list_never_leaks_it(realdb):
    control_mk = realdb.control_sessionmaker()
    sub_id = None
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await _create_sub(c)
            assert resp.status_code == 201, resp.text
            body = resp.json()
            secret = body["signing_secret"]
            sub_id = uuid.UUID(body["subscription"]["id"])
            assert secret.startswith(f"{SECRET_BRAND}_")
            assert "signing_secret" not in body["subscription"]
            assert body["subscription"]["secret_prefix"] == secret[:SECRET_PREFIX_LEN]

            # List never returns the full secret.
            listed = (await c.get("/api/webhooks")).json()
            assert any(row["id"] == str(sub_id) for row in listed)
            for row in listed:
                assert "signing_secret" not in row
                assert secret not in str(row)
    finally:
        if sub_id:
            await _cleanup(control_mk, sub_id)


@pytest.mark.asyncio
async def test_create_validates_url_and_event_types(realdb):
    async with realdb.client(key="a", role="admin") as c:
        # Non-http URL rejected.
        bad_url = await _create_sub(c, target_url="ftp://nope")
        assert bad_url.status_code == 422
        # Unknown event type rejected.
        bad_evt = await _create_sub(c, event_types=["not.a.real.event"])
        assert bad_evt.status_code == 422


# NOTE: create/update SSRF-rejection coverage (metadata / loopback / RFC1918
# targets → non-enumerating 422) lives in test_webhook_url_guard.py, which
# forces the guard ON (flag off) with stubbed resolution. It can't live in this
# module — the autouse _allow_test_targets fixture disables the address checks
# for the legacy `example.test` targets used here.


@pytest.mark.asyncio
async def test_management_is_admin_gated(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        assert (await _create_sub(c)).status_code == 403
        assert (await c.get("/api/webhooks")).status_code == 403
        assert (await c.get("/api/webhooks/deliveries")).status_code == 403


@pytest.mark.asyncio
async def test_cross_tenant_isolation(realdb):
    control_mk = realdb.control_sessionmaker()
    sub_a = None
    try:
        # Admin A creates a subscription.
        async with realdb.client(key="a", role="admin") as c:
            body = (await _create_sub(c)).json()
            sub_a = uuid.UUID(body["subscription"]["id"])

        # Admin B cannot see or mutate it.
        async with realdb.client(key="b", role="admin") as c:
            listed = (await c.get("/api/webhooks")).json()
            assert all(row["id"] != str(sub_a) for row in listed)
            # Same opaque 404 for wrong-org as for missing.
            assert (await c.delete(f"/api/webhooks/{sub_a}")).status_code == 404
            patched = await c.patch(f"/api/webhooks/{sub_a}", json={"active": False})
            assert patched.status_code == 404
    finally:
        if sub_a:
            await _cleanup(control_mk, sub_a)


@pytest.mark.asyncio
async def test_update_and_delete(realdb):
    control_mk = realdb.control_sessionmaker()
    sub_id = None
    try:
        async with realdb.client(key="a", role="admin") as c:
            sub_id = uuid.UUID((await _create_sub(c)).json()["subscription"]["id"])

            upd = await c.patch(
                f"/api/webhooks/{sub_id}",
                json={"active": False, "event_types": [EVENT_PAYMENT_SETTLED]},
            )
            assert upd.status_code == 200
            assert upd.json()["active"] is False
            assert upd.json()["event_types"] == [EVENT_PAYMENT_SETTLED]

            assert (await c.delete(f"/api/webhooks/{sub_id}")).status_code == 204
            # Gone — subsequent delete is a 404.
            assert (await c.delete(f"/api/webhooks/{sub_id}")).status_code == 404
        sub_id = None  # already deleted
    finally:
        if sub_id:
            await _cleanup(control_mk, sub_id)


@pytest.mark.asyncio
async def test_redeliver_requeues_dead_delivery(realdb, monkeypatch):
    org_id = realdb.info("a").org_id
    control_mk = realdb.control_sessionmaker()
    sub, dlv = _make_sub_and_delivery(org_id)
    dlv.status = DELIVERY_DEAD
    dlv.attempt_count = delivery_mod.MAX_ATTEMPTS
    dlv.next_attempt_at = None
    await _persist(control_mk, sub, dlv)

    # The inline redelivery attempt now succeeds.
    async def fake_post(*a, **k):
        return 200

    monkeypatch.setattr(delivery_mod, "_post", fake_post)

    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.post(f"/api/webhooks/deliveries/{dlv.id}/redeliver")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # Re-attempted inline → delivered, counter reset then incremented once.
            assert body["status"] == DELIVERY_DELIVERED
            assert body["attempt_count"] == 1

            # A delivered delivery cannot be redelivered (would double-fire).
            again = await c.post(f"/api/webhooks/deliveries/{dlv.id}/redeliver")
            assert again.status_code == 409
    finally:
        await _cleanup(control_mk, sub.id)


@pytest.mark.asyncio
async def test_list_deliveries_is_org_scoped(realdb):
    org_a = realdb.info("a").org_id
    org_b = realdb.info("b").org_id
    control_mk = realdb.control_sessionmaker()
    sub_a, dlv_a = _make_sub_and_delivery(org_a)
    sub_b, dlv_b = _make_sub_and_delivery(org_b)
    await _persist(control_mk, sub_a, dlv_a, sub_b, dlv_b)

    try:
        async with realdb.client(key="a", role="admin") as c:
            rows = (await c.get("/api/webhooks/deliveries")).json()
            ids = {r["id"] for r in rows}
            assert str(dlv_a.id) in ids
            assert str(dlv_b.id) not in ids
    finally:
        await _cleanup(control_mk, sub_a.id, sub_b.id)


def test_payload_money_is_string_not_float():
    """The emitted payload serialises money as an exact string (money-is-exact)."""
    from decimal import Decimal

    from app.services.webhooks.dispatch import _money_str

    assert _money_str(Decimal("123.45")) == "123.45"
    assert _money_str(None) is None
    # And it round-trips through json without a float.
    assert json.loads(json.dumps({"amount": _money_str(Decimal("0.10"))}))["amount"] == "0.10"
