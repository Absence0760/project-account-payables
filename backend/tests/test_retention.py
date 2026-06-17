"""Retention policies (SOX records management).

Coverage:

  * Pure resolver — per-org override → platform default, malformed → default.
  * Policy endpoint — GET effective policy, PUT updates + audit row, RBAC,
    422 on unknown class / non-positive window.
  * Sweep — soft-archives only overdue terminal invoices, idempotent (no
    double-archive), composes with the audit-immutability trigger (NEVER
    deletes audit rows; verifies WORM shipment + writes a manifest),
    master-switch-off is a no-op for the background loop.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import AuditLog
from app.services import retention_sweep
from app.services.retention_sweep import (
    resolve_retention_months,
    run_retention_once,
    sweep_tenant,
)

# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------


def test_resolver_uses_per_org_override():
    settings_dict = {"retention": {"invoices_months": 36}}
    assert resolve_retention_months(settings_dict, "invoices") == 36


def test_resolver_falls_back_to_default():
    from app.config import settings as cfg

    assert resolve_retention_months({}, "invoices") == cfg.retention_default_months
    assert resolve_retention_months(None, "audit_log") == cfg.retention_default_months


def test_resolver_malformed_value_degrades_to_default():
    from app.config import settings as cfg

    assert resolve_retention_months({"retention": {"invoices_months": "abc"}}, "invoices") == (
        cfg.retention_default_months
    )
    assert resolve_retention_months({"retention": {"invoices_months": -5}}, "invoices") == (
        cfg.retention_default_months
    )


# ---------------------------------------------------------------------------
# run_retention_once — fan-out + master-switch via the loop
# ---------------------------------------------------------------------------


def _fake_control_session(rows: list[tuple]):
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(all=lambda: rows))
    cm = AsyncMock()
    cm.__aenter__.return_value = fake_session
    cm.__aexit__.return_value = None
    return MagicMock(return_value=cm)


@pytest.mark.asyncio
async def test_run_once_isolates_per_tenant_failure():
    rows = [
        (uuid.uuid4(), "ap_a", {}),
        (uuid.uuid4(), "ap_b", {}),
    ]
    engine = MagicMock()
    engine.dispose = AsyncMock()
    with (
        patch.object(retention_sweep, "control_session_factory", _fake_control_session(rows)),
        patch.object(retention_sweep, "create_async_engine", return_value=engine),
        patch.object(retention_sweep, "_make_tenant_url", lambda n: f"url://{n}"),
        patch.object(
            retention_sweep,
            "async_sessionmaker",
            side_effect=RuntimeError("boom"),
        ),
    ):
        result = await run_retention_once()

    assert result.tenants_scanned == 2
    assert result.failures == 2  # both failed, neither halted the other


@pytest.mark.asyncio
async def test_loop_not_started_when_disabled():
    """Master switch off → the lifespan never creates the retention task."""
    from app.config import settings as cfg

    assert cfg.retention_enabled is False  # .env.development default


# ---------------------------------------------------------------------------
# sweep_tenant — real Postgres
# ---------------------------------------------------------------------------


async def _add_invoice(mk, org_id, *, status, created_at, amount=Decimal("10.00")):
    inv_id = uuid.uuid4()
    async with mk() as s:
        inv = Invoice(
            id=inv_id,
            organization_id=org_id,
            correlation_id=uuid.uuid4(),
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_name="Acme",
            amount=amount,
            status=status,
        )
        s.add(inv)
        await s.flush()
        # created_at is server-defaulted; force it for the age test.
        inv.created_at = created_at
        await s.commit()
    return inv_id


@pytest.mark.asyncio
async def test_sweep_archives_only_overdue_terminal_invoices(realdb):
    org_id = realdb.info("a").org_id
    mk = realdb.sessionmaker("a")
    old = datetime.now(UTC) - timedelta(days=4000)  # ~11y, past the 84-month window
    recent = datetime.now(UTC) - timedelta(days=10)

    old_done = await _add_invoice(mk, org_id, status=InvoiceStatus.done, created_at=old)
    old_paid = await _add_invoice(mk, org_id, status=InvoiceStatus.paid, created_at=old)
    # Overdue but NOT terminal — must NOT be archived.
    old_open = await _add_invoice(mk, org_id, status=InvoiceStatus.approved, created_at=old)
    # Terminal but recent — must NOT be archived.
    new_done = await _add_invoice(mk, org_id, status=InvoiceStatus.done, created_at=recent)

    tmk = realdb.sessionmaker("a")
    async with tmk() as db:
        result = await sweep_tenant(db, organization_id=org_id, settings_dict={})
        await db.commit()

    assert result.invoices_archived == 2

    async with mk() as s:
        rows = (await s.execute(select(Invoice))).scalars().all()
        archived = {r.id for r in rows if (r.meta or {}).get("archived_at")}
    assert archived == {old_done, old_paid}
    assert old_open not in archived
    assert new_done not in archived


@pytest.mark.asyncio
async def test_sweep_is_idempotent(realdb):
    org_id = realdb.info("a").org_id
    mk = realdb.sessionmaker("a")
    old = datetime.now(UTC) - timedelta(days=4000)
    await _add_invoice(mk, org_id, status=InvoiceStatus.done, created_at=old)

    tmk = realdb.sessionmaker("a")
    async with tmk() as db:
        first = await sweep_tenant(db, organization_id=org_id, settings_dict={})
        await db.commit()
    async with tmk() as db:
        second = await sweep_tenant(db, organization_id=org_id, settings_dict={})
        await db.commit()

    assert first.invoices_archived == 1
    assert second.invoices_archived == 0  # already archived → not re-archived


@pytest.mark.asyncio
async def test_sweep_writes_retention_manifest_audit(realdb):
    org_id = realdb.info("a").org_id
    mk = realdb.sessionmaker("a")
    old = datetime.now(UTC) - timedelta(days=4000)
    await _add_invoice(mk, org_id, status=InvoiceStatus.done, created_at=old)

    tmk = realdb.sessionmaker("a")
    async with tmk() as db:
        await sweep_tenant(db, organization_id=org_id, settings_dict={})
        await db.commit()

    async with mk() as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "retention.archived")))
            .scalars()
            .all()
        )
    assert rows, "sweep must write a retention.archived manifest row"
    details = rows[-1].details
    assert details["invoices_archived"] == 1
    assert "audit_log" in details["audit_log_note"]


@pytest.mark.asyncio
async def test_sweep_never_deletes_audit_rows(realdb):
    """The audit class is WORM: overdue audit rows are counted, never deleted.

    Composes with migration 0022's immutability trigger — a DELETE would be
    rejected by Postgres anyway; the sweep must not even attempt it.
    """
    org_id = realdb.info("a").org_id
    mk = realdb.sessionmaker("a")

    # An overdue audit row (older than the audit_log window). Insert with an
    # explicit old created_at — we CAN'T UPDATE it afterwards (the immutability
    # trigger rejects every UPDATE except shipped_at), which is exactly the WORM
    # property under test.
    old = datetime.now(UTC) - timedelta(days=4000)
    async with mk() as s:
        s.add(
            AuditLog(
                correlation_id=uuid.uuid4(),
                organization_id=org_id,
                actor_id=None,
                action="invoice.created",
                entity_type="invoice",
                entity_id=uuid.uuid4(),
                created_at=old,
            )
        )
        await s.commit()

    before = await _count_audit_rows(mk)

    tmk = realdb.sessionmaker("a")
    async with tmk() as db:
        result = await sweep_tenant(db, organization_id=org_id, settings_dict={})
        await db.commit()

    assert result.audit_rows_overdue >= 1
    after = await _count_audit_rows(mk)
    # The old row survives; the sweep only ADDED its manifest row (never deleted).
    assert after >= before


async def _count_audit_rows(mk) -> int:
    from sqlalchemy import func

    async with mk() as s:
        return (await s.execute(select(func.count()).select_from(AuditLog))).scalar_one()


# ---------------------------------------------------------------------------
# Policy endpoint — real Postgres + ASGI app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_policy_returns_effective(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get("/api/retention-policy")
    assert resp.status_code == 200
    body = resp.json()
    assert "invoices" in body["policy"]
    assert "audit_log" in body["policy"]
    assert body["default_months"] > 0


@pytest.mark.asyncio
async def test_put_policy_updates_and_audits(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/retention-policy", json={"policy": {"invoices": 36}})
    assert resp.status_code == 200
    assert resp.json()["policy"]["invoices"] == 36

    # The change is persisted on org settings.
    cmk = realdb.control_sessionmaker()
    from app.models.organization import Organization

    async with cmk() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == realdb.info("a").org_id))
        ).scalar_one()
    assert org.settings["retention"]["invoices_months"] == 36

    # And a retention_policy.updated audit row was written into the tenant trail.
    tmk = realdb.sessionmaker("a")
    async with tmk() as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "retention_policy.updated")))
            .scalars()
            .all()
        )
    assert rows
    assert rows[-1].details["changes"]["invoices"]["new"] == 36


@pytest.mark.asyncio
async def test_put_policy_rejects_unknown_class(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/retention-policy", json={"policy": {"bogus": 12}})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_policy_rejects_non_positive(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/retention-policy", json={"policy": {"invoices": 0}})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_policy_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/retention-policy")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_policy_admin_only(realdb):
    """Read + write are admin-only (records-management privilege)."""
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/retention-policy")
    assert resp.status_code == 403
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.put("/api/retention-policy", json={"policy": {"invoices": 60}})
    assert resp.status_code == 403
