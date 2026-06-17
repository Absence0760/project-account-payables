"""Digital signatures on approvals (SOX non-repudiation).

Two layers:

  * Pure unit tests of the ``approval_signature`` primitives — sign/verify
    round-trip, tamper detection (amount / actor / timestamp), determinism,
    money exactness, and the no-key fail-closed posture.
  * Real-Postgres endpoint tests of
    ``GET /api/audit/invoice/{id}/verify-signatures`` — a genuine
    signature seeded on an ``invoice.approved`` row verifies valid; a
    post-approval amount tamper flips it invalid; RBAC + access-audit hold.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import AuditLog
from app.services import approval_signature as sig
from app.services.approval_signature import (
    build_signature_detail,
    sign_approval,
    verify_approval,
)
from app.services.audit import log_action

KEY = "unit-test-signing-key"


def _facts(**over):
    base = dict(
        invoice_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        amount=Decimal("1234.56"),
        actor_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        decision="approved",
        timestamp=datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC),
    )
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Pure primitives
# ---------------------------------------------------------------------------


def test_sign_then_verify_roundtrips():
    facts = _facts()
    signature = sign_approval(signing_key=KEY, **facts)
    assert len(signature) == 64  # hex sha256
    assert verify_approval(signing_key=KEY, signature=signature, **facts) is True


def test_signature_is_deterministic():
    facts = _facts()
    assert sign_approval(signing_key=KEY, **facts) == sign_approval(signing_key=KEY, **facts)


def test_tampered_amount_fails_verification():
    facts = _facts()
    signature = sign_approval(signing_key=KEY, **facts)
    tampered = _facts(amount=Decimal("9999.99"))
    assert verify_approval(signing_key=KEY, signature=signature, **tampered) is False


def test_tampered_actor_fails_verification():
    facts = _facts()
    signature = sign_approval(signing_key=KEY, **facts)
    tampered = _facts(actor_id=uuid.uuid4())
    assert verify_approval(signing_key=KEY, signature=signature, **tampered) is False


def test_tampered_timestamp_fails_verification():
    facts = _facts()
    signature = sign_approval(signing_key=KEY, **facts)
    tampered = _facts(timestamp=datetime(2026, 6, 17, 12, 0, 1, tzinfo=UTC))
    assert verify_approval(signing_key=KEY, signature=signature, **tampered) is False


def test_wrong_key_fails_verification():
    facts = _facts()
    signature = sign_approval(signing_key=KEY, **facts)
    assert verify_approval(signing_key="other-key", signature=signature, **facts) is False


def test_money_exact_equal_amounts_same_digest():
    """100.00 and 100.0 are the same money → same canonical digest (no float)."""
    a = sign_approval(signing_key=KEY, **_facts(amount=Decimal("100.00")))
    b = sign_approval(signing_key=KEY, **_facts(amount=Decimal("100.0")))
    assert a == b


def test_empty_key_skips_signing():
    assert sign_approval(signing_key="", **_facts()) == ""
    # verify with no key fails closed (nothing to verify against)
    assert verify_approval(signing_key="", signature="anything", **_facts()) is False


def test_empty_signature_fails_closed():
    assert verify_approval(signing_key=KEY, signature=None, **_facts()) is False
    assert verify_approval(signing_key=KEY, signature="", **_facts()) is False


def test_build_signature_detail_shape():
    block = build_signature_detail(signing_key=KEY, **_facts())
    assert block["alg"] == sig.SIGNATURE_ALG
    assert block["signed_fields"] == sig.SIGNED_FIELDS
    assert block["signed_at"] == _facts()["timestamp"].isoformat()
    assert len(block["value"]) == 64
    # No raw amount / PII in the block — just field NAMES.
    assert "amount" not in {k for k in block if k != "signed_fields"}


def test_build_signature_detail_none_without_key():
    assert build_signature_detail(signing_key="", **_facts()) is None


# ---------------------------------------------------------------------------
# Endpoint — real Postgres + ASGI app
# ---------------------------------------------------------------------------


async def _seed_signed_approval(
    mk, org_id, *, amount: Decimal, actor_id: uuid.UUID, key: str
) -> tuple[uuid.UUID, datetime]:
    """Seed an invoice + a genuinely-signed ``invoice.approved`` audit row."""
    corr = uuid.uuid4()
    inv_id = uuid.uuid4()
    signed_at = datetime.now(UTC)
    block = build_signature_detail(
        invoice_id=inv_id,
        amount=amount,
        actor_id=actor_id,
        decision="approved",
        timestamp=signed_at,
        signing_key=key,
    )
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                correlation_id=corr,
                invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
                vendor_name="Acme",
                amount=amount,
                status=InvoiceStatus.approved,
            )
        )
        await log_action(
            s,
            correlation_id=corr,
            organization_id=org_id,
            actor_id=actor_id,
            action="invoice.approved",
            entity_type="invoice",
            entity_id=inv_id,
            details={"signature": block},
        )
        await s.commit()
    return inv_id, signed_at


@pytest.mark.asyncio
async def test_verify_endpoint_reports_valid(realdb, monkeypatch):
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "approval_signing_key", "endpoint-key")
    mk = realdb.sessionmaker("a")
    actor = realdb.info("a").users["ap_manager"]
    inv_id, _ = await _seed_signed_approval(
        mk, realdb.info("a").org_id, amount=Decimal("500.00"), actor_id=actor, key="endpoint-key"
    )

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/audit/invoice/{inv_id}/verify-signatures")

    assert resp.status_code == 200
    body = resp.json()
    assert body["signing_configured"] is True
    assert len(body["approvals"]) == 1
    row = body["approvals"][0]
    assert row["signed"] is True
    assert row["valid"] is True


@pytest.mark.asyncio
async def test_verify_endpoint_detects_amount_tamper(realdb, monkeypatch):
    """Change the invoice amount AFTER approval → signature no longer verifies."""
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "approval_signing_key", "endpoint-key")
    mk = realdb.sessionmaker("a")
    actor = realdb.info("a").users["ap_manager"]
    inv_id, _ = await _seed_signed_approval(
        mk, realdb.info("a").org_id, amount=Decimal("500.00"), actor_id=actor, key="endpoint-key"
    )

    # Tamper: bump the persisted invoice amount.
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        inv.amount = Decimal("5000.00")
        await s.commit()

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/audit/invoice/{inv_id}/verify-signatures")

    assert resp.status_code == 200
    row = resp.json()["approvals"][0]
    assert row["signed"] is True
    assert row["valid"] is False  # tamper detected


@pytest.mark.asyncio
async def test_verify_endpoint_writes_access_audit(realdb, monkeypatch):
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "approval_signing_key", "endpoint-key")
    mk = realdb.sessionmaker("a")
    actor = realdb.info("a").users["ap_manager"]
    inv_id, _ = await _seed_signed_approval(
        mk, realdb.info("a").org_id, amount=Decimal("12.00"), actor_id=actor, key="endpoint-key"
    )

    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get(f"/api/audit/invoice/{inv_id}/verify-signatures")
    assert resp.status_code == 200

    async with mk() as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "audit.viewed")))
            .scalars()
            .all()
        )
    assert any((r.details or {}).get("verify_signatures") for r in rows)


@pytest.mark.asyncio
async def test_verify_endpoint_unknown_invoice_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/audit/invoice/{uuid.uuid4()}/verify-signatures")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_endpoint_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get(f"/api/audit/invoice/{uuid.uuid4()}/verify-signatures")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_verify_endpoint_clerk_forbidden(realdb):
    """Admin/CFO only — the auditor privilege. A clerk gets 403."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/audit/invoice/{uuid.uuid4()}/verify-signatures")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_real_approval_flow_signs_and_verifies(realdb, monkeypatch):
    """End-to-end: approve through the real service → row carries a signature
    that the verify endpoint confirms valid."""
    from app.config import settings as cfg
    from app.services.review import approve_invoice

    monkeypatch.setattr(cfg, "approval_signing_key", "flow-key")
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor = realdb.info("a").users["ap_manager"]

    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
                invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
                vendor_name="Acme",
                amount=Decimal("777.77"),
                status=InvoiceStatus.ready_for_review,
            )
        )
        await s.commit()

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await approve_invoice(
            s, inv, actor_id=actor, actor_name="Manager", actor_roles={"ap_manager"}
        )
        await s.commit()

    # The approved row carries a signature block.
    async with mk() as s:
        row = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == inv_id, AuditLog.action == "invoice.approved"
                )
            )
        ).scalar_one()
    assert row.details["signature"]["alg"] == sig.SIGNATURE_ALG

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/audit/invoice/{inv_id}/verify-signatures")
    assert resp.status_code == 200
    assert resp.json()["approvals"][0]["valid"] is True
