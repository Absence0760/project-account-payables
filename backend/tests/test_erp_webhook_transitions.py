"""ERP webhook transition-guard tests.

Regression coverage for the divergent-transition-map defect: the handler used
to keep its own local `valid_transitions` dict that permitted edges the
authoritative `workflow_engine.VALID_TRANSITIONS` forbids (e.g.
`posted_in_erp → paid`, `posted_in_erp → failed`). When the ERP reported such a
status, `transition_invoice` raised `HTTPException(409)` and the handler
re-raised it — breaking the documented "every webhook rejection path returns 204
silently" invariant AND corrupting nothing (no transition applied).

These tests assert that:
  * an ERP status whose transition the state machine FORBIDS for the invoice's
    current state returns a silent 204 (not a 409/500) and does NOT mutate state
    or write an audit row (the specific `posted_in_erp` + `voided` case from the
    finding, plus `posted_in_erp` + `paid`), and
  * a legitimately-permitted ERP-driven transition still applies AND goes through
    `transition_invoice` (the audit chokepoint).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.invoice import InvoiceStatus


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _fake_request(body: bytes, headers: dict | None = None):
    req = MagicMock()
    req.body = AsyncMock(return_value=body)

    async def _json():
        return json.loads(body.decode("utf-8"))

    req.json = _json
    req.headers = headers or {}
    return req


def _ctrl_session_for_org(org):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=org)
    ctrl_db = AsyncMock()
    ctrl_db.execute = AsyncMock(return_value=result)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=ctrl_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _fake_tenant_session_factory(invoice):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=invoice)
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, db


def _org_with_erp_secret(secret: str):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug="acme",
        db_name="ap_acme",
        settings={"erp": {"webhook_signing_secret": secret}},
    )


class _FakeRedis:
    """Minimal SET NX EX / get store for the dedup helper."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def get(self, key):
        return self._store.get(key)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.services.webhook_security.get_redis", _get_redis)
    return fake


async def _post(*, org, invoice, status_value, event_id, secret="erp-secret"):
    """Drive `erp_webhook` with a correctly-signed body for `status_value`.

    Returns (result, tenant_db, transition_calls) where transition_calls records
    the args every `transition_invoice` invocation received (empty if the guard
    short-circuited before it).
    """
    from app.api.erp_webhook import erp_webhook

    body = {
        "tenant_slug": "acme",
        "correlation_id": str(uuid.uuid4()),
        "erp_document_id": "doc_1",
        "status": status_value,
        "event_id": event_id,
    }
    body_bytes = json.dumps(body).encode("utf-8")
    sig = _sign(secret, body_bytes)

    tenant_factory, db = _fake_tenant_session_factory(invoice)
    transition_calls: list[dict] = []

    async def _capture(_db, inv, target, *, action_name, details):
        transition_calls.append(
            {"invoice": inv, "target": target, "action_name": action_name, "details": details}
        )
        inv.status = target
        return inv

    with (
        patch("app.api.erp_webhook.control_session_factory", _ctrl_session_for_org(org)),
        patch("app.api.erp_webhook.get_tenant_engine", return_value=MagicMock()),
        patch("app.api.erp_webhook.async_sessionmaker", return_value=tenant_factory),
        patch("app.api.erp_webhook.transition_invoice", _capture),
    ):
        result = await erp_webhook(
            erp_type="generic",
            request=_fake_request(body_bytes, {"X-Webhook-Signature": sig}),
        )
    return result, db, transition_calls


@pytest.mark.asyncio
async def test_forbidden_transition_posted_to_voided_is_silent_204(fake_redis):
    """The finding's exact case: ERP reports `voided` (→ failed) for an invoice
    already `posted_in_erp`. VALID_TRANSITIONS forbids `posted_in_erp → failed`,
    so the webhook must silently 204 — never raise the 409 the old divergent map
    provoked — and must NOT mutate the invoice or write an audit row."""
    org = _org_with_erp_secret("erp-secret")
    invoice = SimpleNamespace(status=InvoiceStatus.posted_in_erp)

    result, db, transition_calls = await _post(
        org=org, invoice=invoice, status_value="voided", event_id="ev_void"
    )

    assert result is None  # silent 204, not a raised HTTPException
    assert invoice.status is InvoiceStatus.posted_in_erp  # state uncorrupted
    assert transition_calls == []  # never reached the transition/audit chokepoint
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_forbidden_transition_posted_to_paid_is_silent_204(fake_redis):
    """`posted_in_erp → paid` was permitted by the old local map but is NOT in
    the canonical machine (`posted_in_erp` → payment_scheduled | done). It must
    now silently no-op rather than 409."""
    org = _org_with_erp_secret("erp-secret")
    invoice = SimpleNamespace(status=InvoiceStatus.posted_in_erp)

    result, db, transition_calls = await _post(
        org=org, invoice=invoice, status_value="paidInFull", event_id="ev_paid"
    )

    assert result is None
    assert invoice.status is InvoiceStatus.posted_in_erp
    assert transition_calls == []
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_permitted_transition_sent_to_posted_applies_and_audits(fake_redis):
    """A legitimately-permitted ERP-driven transition still applies. ERP reports
    `Open` (→ posted_in_erp) for an invoice at `sent_to_erp` — a legal edge —
    so it must go through transition_invoice (the audit chokepoint) and commit."""
    org = _org_with_erp_secret("erp-secret")
    invoice = SimpleNamespace(status=InvoiceStatus.sent_to_erp)

    result, db, transition_calls = await _post(
        org=org, invoice=invoice, status_value="Open", event_id="ev_open"
    )

    assert result is None
    assert len(transition_calls) == 1
    call = transition_calls[0]
    assert call["target"] is InvoiceStatus.posted_in_erp
    assert call["action_name"] == "invoice.erp_status_posted_in_erp"
    # PII-free whitelisted audit details only.
    assert set(call["details"]) == {"erp_type", "erp_status", "erp_document_id", "raw_event_id"}
    assert invoice.status is InvoiceStatus.posted_in_erp
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_permitted_transition_scheduled_to_paid_applies(fake_redis):
    """`payment_scheduled → paid` is a canonical edge (ERP reports paid). Still
    applies + commits after the guard change."""
    org = _org_with_erp_secret("erp-secret")
    invoice = SimpleNamespace(status=InvoiceStatus.payment_scheduled)

    result, db, transition_calls = await _post(
        org=org, invoice=invoice, status_value="paid", event_id="ev_paid2"
    )

    assert result is None
    assert len(transition_calls) == 1
    assert transition_calls[0]["target"] is InvoiceStatus.paid
    assert invoice.status is InvoiceStatus.paid
    db.commit.assert_awaited()
