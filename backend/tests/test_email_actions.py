"""Integration tests for email approval — the public confirm/perform endpoints
and the assigned-invoice email link injection.

Exercises the real ASGI app against a live Postgres (``realdb``):
  - GET renders a confirmation page and does NOT mutate (prefetch-safe);
  - POST approve → invoice approved + immutable audit row, single-use replay;
  - POST reject (with reason) → invoice rejected + exception row;
  - invalid / tampered / no-key tokens → friendly 400, no mutation;
  - segregation of duties + non-approver role are enforced (no weaker door);
  - wrong-status invoice is a no-op;
  - the invoice_assigned email carries a working Approve/Reject link.

Auth-gating is covered separately by test_rbac.py (both routes are public).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.config import settings
from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import AuditLog
from app.services.email_action_token import (
    ACTION_APPROVE,
    ACTION_REJECT,
    build_action_token,
    verify_action_token,
)

pytestmark = pytest.mark.asyncio

_KEY = "integration-email-action-key"


@pytest_asyncio.fixture
async def fresh_control_factory(realdb, monkeypatch):
    """Point the module-global ``control_session_factory`` (used by
    ``notify_event`` / ``_resolve_org_slug``) at an engine bound to THIS test's
    loop AND this slot's own control-plane DB — mirrors the same fixture in
    test_notification_dispatch.py. Must go through ``realdb.control_sessionmaker()``
    (not a bare ``create_async_engine(settings.database_url)``): the harness's
    orgs live in this process's per-slot control-plane database, not the real,
    shared one — see ``control_db_name_for_slot`` in conftest.py."""
    monkeypatch.setattr(
        "app.database.control_session_factory",
        realdb.control_sessionmaker(),
    )
    yield


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setattr(settings, "email_action_signing_key", _KEY)
    monkeypatch.setattr(settings, "email_action_ttl_hours", 168)
    return _KEY


async def _make_invoice(
    realdb,
    *,
    status: InvoiceStatus = InvoiceStatus.ready_for_review,
    uploaded_by_id: uuid.UUID | None = None,
    amount: str = "100.00",
    number: str | None = None,
) -> uuid.UUID:
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = Invoice(
            invoice_number=number or f"EA-{uuid.uuid4().hex[:8]}",
            vendor_name="Test Vendor",
            amount=Decimal(amount),
            currency="USD",
            status=status,
            organization_id=info.org_id,
            uploaded_by_id=uploaded_by_id,
        )
        s.add(inv)
        await s.commit()
        return inv.id


def _token(realdb, invoice_id, *, action=ACTION_APPROVE, role="ap_manager", key=_KEY):
    info = realdb.info("a")
    return build_action_token(
        tenant_slug=info.slug,
        invoice_id=invoice_id,
        actor_id=info.users[role],
        action=action,
        signing_key=key,
        ttl_hours=168,
    )


async def _status(realdb, invoice_id) -> str:
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        return (
            await s.execute(select(Invoice.status).where(Invoice.id == invoice_id))
        ).scalar_one()


# ---------------------------------------------------------------------------
# GET confirmation page — read-only
# ---------------------------------------------------------------------------


async def test_get_confirm_page_renders_and_does_not_mutate(realdb, signing_key):
    admin = realdb.info("a").users["admin"]
    inv_id = await _make_invoice(realdb, uploaded_by_id=admin)
    token = _token(realdb, inv_id)

    async with realdb.client(key="a", role=None) as c:
        resp = await c.get(f"/api/invoices/email-action/{token}")
    assert resp.status_code == 200
    assert "Confirm approve" in resp.text
    # Prefetch safety: a bare GET must not approve.
    assert await _status(realdb, inv_id) == InvoiceStatus.ready_for_review


async def test_get_invalid_token_shows_invalid_page(realdb, signing_key):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/invoices/email-action/garbage.deadbeef")
    assert resp.status_code == 400
    assert "invalid or has expired" in resp.text


# ---------------------------------------------------------------------------
# POST approve
# ---------------------------------------------------------------------------


async def test_post_approve_happy_path_and_single_use(realdb, signing_key):
    admin = realdb.info("a").users["admin"]
    inv_id = await _make_invoice(realdb, uploaded_by_id=admin)
    token = _token(realdb, inv_id)

    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(f"/api/invoices/email-action/{token}/confirm")
        assert resp.status_code == 200
        assert "approved" in resp.text.lower()
        assert await _status(realdb, inv_id) == InvoiceStatus.approved

        # Immutable audit row written by the normal review path.
        mk = realdb.sessionmaker("a")
        async with mk() as s:
            n = await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == inv_id, AuditLog.action == "invoice.approved")
            )
            assert n.scalar_one() == 1

        # Single-use: the same link cannot be replayed.
        replay = await c.post(f"/api/invoices/email-action/{token}/confirm")
        assert replay.status_code == 200
        assert "already been used" in replay.text.lower()


async def test_post_tampered_token_rejected(realdb, signing_key):
    inv_id = await _make_invoice(realdb)
    token = _token(realdb, inv_id)
    body, _, sig = token.rpartition(".")
    bad = f"{body}.{sig[:-1]}" + ("a" if sig[-1] != "a" else "b")
    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(f"/api/invoices/email-action/{bad}/confirm")
    assert resp.status_code == 400
    assert await _status(realdb, inv_id) == InvoiceStatus.ready_for_review


async def test_no_key_configured_rejects_token(realdb, monkeypatch):
    # Build a valid token, then disable the feature — every token must fail closed.
    monkeypatch.setattr(settings, "email_action_signing_key", _KEY)
    inv_id = await _make_invoice(realdb)
    token = _token(realdb, inv_id)
    monkeypatch.setattr(settings, "email_action_signing_key", "")
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get(f"/api/invoices/email-action/{token}")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST reject
# ---------------------------------------------------------------------------


async def test_post_reject_with_reason(realdb, signing_key):
    admin = realdb.info("a").users["admin"]
    inv_id = await _make_invoice(realdb, uploaded_by_id=admin)
    token = _token(realdb, inv_id, action=ACTION_REJECT)

    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(
            f"/api/invoices/email-action/{token}/confirm",
            data={"reason": "Wrong amount"},
        )
    assert resp.status_code == 200
    assert "rejected" in resp.text.lower()
    assert await _status(realdb, inv_id) == InvoiceStatus.rejected

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        exc = (
            await s.execute(
                select(APException).where(
                    APException.invoice_id == inv_id,
                    APException.exception_type == "review_rejected",
                )
            )
        ).scalar_one()
        assert exc.description == "Wrong amount"


# ---------------------------------------------------------------------------
# Authorization parity — segregation + role gate
# ---------------------------------------------------------------------------


async def test_segregation_blocks_self_approval(realdb, signing_key):
    # Reviewer is the uploader → SoD must block, same as the in-app path.
    reviewer = realdb.info("a").users["ap_manager"]
    inv_id = await _make_invoice(realdb, uploaded_by_id=reviewer)
    token = _token(realdb, inv_id, role="ap_manager")

    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(f"/api/invoices/email-action/{token}/confirm")
    assert resp.status_code == 200
    assert "not allowed" in resp.text.lower() or "segregation" in resp.text.lower()
    assert await _status(realdb, inv_id) == InvoiceStatus.ready_for_review


async def test_non_approver_role_rejected(realdb, signing_key):
    admin = realdb.info("a").users["admin"]
    inv_id = await _make_invoice(realdb, uploaded_by_id=admin)
    token = _token(realdb, inv_id, role="ap_clerk")  # clerk can't approve

    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(f"/api/invoices/email-action/{token}/confirm")
    assert resp.status_code == 200
    assert "not permitted" in resp.text.lower()
    assert await _status(realdb, inv_id) == InvoiceStatus.ready_for_review


async def test_wrong_status_is_noop(realdb, signing_key):
    inv_id = await _make_invoice(realdb, status=InvoiceStatus.approved)
    token = _token(realdb, inv_id)
    async with realdb.client(key="a", role=None) as c:
        get = await c.get(f"/api/invoices/email-action/{token}")
        assert "no longer awaiting review" in get.text.lower()
        post = await c.post(f"/api/invoices/email-action/{token}/confirm")
        assert "no longer awaiting review" in post.text.lower()
    assert await _status(realdb, inv_id) == InvoiceStatus.approved


# ---------------------------------------------------------------------------
# Email link injection
# ---------------------------------------------------------------------------


async def test_assigned_email_includes_action_links(
    realdb, signing_key, fresh_control_factory, monkeypatch
):
    from app.models.notification import EVENT_INVOICE_ASSIGNED
    from app.services import notification_dispatch
    from app.services.notification_templates import InvoiceContext

    captured = []

    class _CapturingAdapter:
        async def send(self, message):
            captured.append(message)

    monkeypatch.setattr(
        "app.services.email_adapters.get_email_adapter", lambda: _CapturingAdapter()
    )

    info = realdb.info("a")
    reviewer = info.users["ap_manager"]
    inv_id = await _make_invoice(realdb, uploaded_by_id=info.users["admin"])

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        await notification_dispatch.notify_event(
            s,
            correlation_id=uuid.uuid4(),
            organization_id=info.org_id,
            event_type=EVENT_INVOICE_ASSIGNED,
            entity_id=inv_id,
            recipient_user_ids=[reviewer],
            invoice_ctx=InvoiceContext(
                invoice_number="EA-NOTIFY",
                vendor_name="Test Vendor",
                amount=Decimal("100.00"),
                currency="USD",
            ),
        )

    assert len(captured) == 1
    msg = captured[0]
    assert "/api/invoices/email-action/" in msg.body_text
    assert msg.body_html and "Approve" in msg.body_html
    # The embedded approve token must verify and bind to this reviewer + invoice.
    line = next(ln for ln in msg.body_text.splitlines() if "Approve:" in ln)
    token = line.split("email-action/")[1]
    decoded = verify_action_token(token, _KEY)
    assert decoded is not None
    assert decoded.invoice_id == inv_id
    assert decoded.actor_id == reviewer
    assert decoded.action == ACTION_APPROVE
