"""In-depth security tests for the payment-processor webhook handler.

Per project invariant #9: "Webhook handlers verify signatures and
dedupe by event id." The handler at `POST /api/payments/webhook/
{tenant_slug}/{provider}` is authenticated purely by the processor's
HMAC signature — there's no JWT, no header, no cookie. A regression
here is "Critical" because it turns a one-time effect (a settled
payment) into a replayable money-mover.

We mock the DB sessions but exercise the real handler shape:
   - Unknown tenant slug → silent 204 (no info leak)
   - Wrong provider for tenant → silent 204
   - Bad signature (parse_webhook returns None) → silent 204
   - Unknown provider_payment_id → silent 204 (late retry of a payment
     we don't have)
   - Terminal payment is not downgraded on a re-delivered webhook
   - ERP sync only fires when a payment newly settled
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_request(body: bytes = b"{}", headers: dict | None = None):
    """Minimal mock of the FastAPI `Request` the handler reads from."""
    req = MagicMock()
    req.body = AsyncMock(return_value=body)
    req.headers = headers or {}
    return req


def _ctrl_session_factory(org):
    """Mock the control-plane session factory used to look up the org."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=org)
    ctrl_db = AsyncMock()
    ctrl_db.execute = AsyncMock(return_value=result)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=ctrl_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _tenant_session_factory(payment):
    """Mock the tenant-DB session factory used to look up / mutate
    the Payment row."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=payment)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, db


def _org(slug="acme", provider="modern_treasury", webhook_secret="s3cret"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug=slug,
        db_name=f"ap_{slug}",
        settings={
            "payments": {
                "provider": provider,
                "webhook_signing_secret": webhook_secret,
            }
        },
    )


# ---------------------------------------------------------------------------
# Silent rejection for "no such tenant"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_returns_silently_for_unknown_tenant():
    """A request to /api/payments/webhook/<garbage>/modern_treasury
    must not leak the fact that the tenant doesn't exist. Returning
    a different status / error for missing tenants would let an
    attacker enumerate slugs."""
    from app.api.payments import payment_webhook

    with (
        patch(
            "app.database.control_session_factory",
            _ctrl_session_factory(None),  # tenant not found
        ),
        # Adapter resolution must not be reached.
        patch("app.api.payments.get_payment_adapter") as mk_adapter,
    ):
        result = await payment_webhook(
            tenant_slug="does-not-exist",
            provider="modern_treasury",
            request=_fake_request(),
        )

    assert result is None  # 204 No Content path
    mk_adapter.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_returns_silently_for_wrong_provider():
    """A tenant configured for `modern_treasury` receives a webhook
    POSTed to /.../mock — the handler must silently 204 rather than
    dispatching to the wrong adapter (which could mis-parse the body
    and corrupt state)."""
    from app.api.payments import payment_webhook

    org = _org(provider="modern_treasury")
    with (
        patch(
            "app.database.control_session_factory",
            _ctrl_session_factory(org),
        ),
        patch("app.api.payments.get_payment_adapter") as mk_adapter,
    ):
        result = await payment_webhook(
            tenant_slug="acme",
            provider="mock",  # wrong adapter for this tenant
            request=_fake_request(),
        )

    assert result is None
    mk_adapter.assert_not_called()


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_returns_silently_when_adapter_rejects_signature():
    """`parse_webhook(headers, body)` returns None for any non-
    verifiable payload: bad HMAC, malformed body, unrecognised event
    type. The handler must NOT touch the DB in that case."""
    from app.api.payments import payment_webhook

    org = _org()
    adapter = MagicMock()
    adapter.parse_webhook = MagicMock(return_value=None)  # signature failed

    tenant_factory, db = _tenant_session_factory(payment=None)

    with (
        patch(
            "app.database.control_session_factory",
            _ctrl_session_factory(org),
        ),
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.database.get_tenant_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=tenant_factory),
    ):
        result = await payment_webhook(
            tenant_slug="acme",
            provider="modern_treasury",
            request=_fake_request(body=b'{"forged":"payload"}'),
        )

    assert result is None
    # CRITICAL: the tenant DB must not be opened on a bad signature.
    # Without this, a flood of forged webhooks would still hammer Postgres.
    db.execute.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_passes_headers_and_body_to_adapter_unchanged():
    """The adapter is the HMAC oracle — the handler must forward the
    raw body and headers verbatim. Mutating either would break signature
    verification (the processor signed the bytes-on-the-wire, not a
    parsed copy)."""
    from app.api.payments import payment_webhook

    org = _org()
    adapter = MagicMock()
    adapter.parse_webhook = MagicMock(return_value=None)

    raw_body = b'{"event":"payment_order.settled","id":"evt_1"}'
    headers = {"x-modern-treasury-signature": "abc123", "content-type": "application/json"}

    with (
        patch("app.database.control_session_factory", _ctrl_session_factory(org)),
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
    ):
        await payment_webhook(
            tenant_slug="acme",
            provider="modern_treasury",
            request=_fake_request(body=raw_body, headers=headers),
        )

    adapter.parse_webhook.assert_called_once()
    call_headers, call_body = adapter.parse_webhook.call_args[0]
    assert call_body == raw_body, "body must be forwarded byte-identical"
    assert call_headers["x-modern-treasury-signature"] == "abc123"


# ---------------------------------------------------------------------------
# Dedup / replay protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_does_not_downgrade_terminal_payment():
    """A re-delivered webhook for a payment that already reached
    `completed` (or `failed`, `cancelled`) must NOT roll the status
    back. Processors re-send on any non-2xx, sometimes hours apart;
    the same event id can land twice."""
    from app.api.payments import payment_webhook
    from app.services.payment_adapters import PaymentStatus

    org = _org()
    completed = SimpleNamespace(
        id=uuid.uuid4(),
        provider_payment_id="px_1",
        payment_run_id=uuid.uuid4(),
        status="completed",
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        reference="REF-OLD",
        failure_reason=None,
    )

    adapter = MagicMock()
    adapter.parse_webhook = MagicMock(
        return_value=SimpleNamespace(
            provider_payment_id="px_1",
            status=PaymentStatus.submitted,  # downgrade attempt
            reference="REF-NEW",
            failure_reason=None,
        )
    )

    tenant_factory, db = _tenant_session_factory(completed)

    with (
        patch("app.database.control_session_factory", _ctrl_session_factory(org)),
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.database.get_tenant_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=tenant_factory),
        patch("app.services.payment_erp_sync.dispatch_payment_sync") as mk_sync,
    ):
        await payment_webhook(
            tenant_slug="acme",
            provider="modern_treasury",
            request=_fake_request(),
        )

    # Status untouched, no commit, no ERP fan-out.
    assert completed.status == "completed"
    assert completed.reference == "REF-OLD"
    db.commit.assert_not_called()
    mk_sync.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_does_not_downgrade_failed_payment():
    """Same dedup contract on the `failed` terminal."""
    from app.api.payments import payment_webhook
    from app.services.payment_adapters import PaymentStatus

    org = _org()
    failed = SimpleNamespace(
        id=uuid.uuid4(),
        provider_payment_id="px_2",
        payment_run_id=uuid.uuid4(),
        status="failed",
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        reference=None,
        failure_reason="insufficient_funds",
    )

    adapter = MagicMock()
    adapter.parse_webhook = MagicMock(
        return_value=SimpleNamespace(
            provider_payment_id="px_2",
            status=PaymentStatus.completed,  # late "settled" arriving after failure
            reference="REF-NEW",
            failure_reason=None,
        )
    )

    tenant_factory, db = _tenant_session_factory(failed)
    with (
        patch("app.database.control_session_factory", _ctrl_session_factory(org)),
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.database.get_tenant_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=tenant_factory),
    ):
        await payment_webhook(
            tenant_slug="acme",
            provider="modern_treasury",
            request=_fake_request(),
        )

    assert failed.status == "failed"
    db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Unknown payment / late delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_silently_ignores_unknown_provider_payment_id():
    """A webhook whose `provider_payment_id` doesn't match any row
    must be a no-op — could be a payment we deleted, a test event the
    processor sent before integration, or a malicious crafted body.
    Either way, return 204 with no DB mutation."""
    from app.api.payments import payment_webhook
    from app.services.payment_adapters import PaymentStatus

    org = _org()
    adapter = MagicMock()
    adapter.parse_webhook = MagicMock(
        return_value=SimpleNamespace(
            provider_payment_id="px_unknown",
            status=PaymentStatus.completed,
            reference=None,
            failure_reason=None,
        )
    )

    tenant_factory, db = _tenant_session_factory(payment=None)  # no row
    with (
        patch("app.database.control_session_factory", _ctrl_session_factory(org)),
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.database.get_tenant_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=tenant_factory),
    ):
        await payment_webhook(
            tenant_slug="acme",
            provider="modern_treasury",
            request=_fake_request(),
        )

    db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Tenant isolation via URL path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_only_touches_the_url_path_tenant_db():
    """The tenant comes from the URL path, never from the body. A
    payload that names a different org_id in its metadata must NOT
    redirect the update to that org's DB."""
    from app.api.payments import payment_webhook
    from app.services.payment_adapters import PaymentStatus

    org = _org(slug="acme")
    payment = SimpleNamespace(
        id=uuid.uuid4(),
        provider_payment_id="px_3",
        payment_run_id=None,
        status="submitted",
        completed_at=None,
        reference=None,
        failure_reason=None,
    )

    adapter = MagicMock()
    adapter.parse_webhook = MagicMock(
        return_value=SimpleNamespace(
            provider_payment_id="px_3",
            status=PaymentStatus.completed,
            reference="REF-1",
            failure_reason=None,
        )
    )

    tenant_factory, db = _tenant_session_factory(payment)
    with (
        patch("app.database.control_session_factory", _ctrl_session_factory(org)),
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.database.get_tenant_engine") as mk_engine,
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=tenant_factory),
    ):
        await payment_webhook(
            tenant_slug="acme",
            provider="modern_treasury",
            # The body claims to be for "techflow" — the handler must ignore that.
            request=_fake_request(body=b'{"organization_id":"techflow"}'),
        )

    # The engine was requested for acme's DB, not techflow's.
    assert mk_engine.call_count == 1
    assert mk_engine.call_args[0][0] == "ap_acme"
