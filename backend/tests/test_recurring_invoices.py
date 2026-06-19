"""Tests for recurring / subscription invoices.

Two layers, mirroring the discounting suite:

  * Pure / unit — the period-key + next-run-on scheduler and the variance flag,
    against ``SimpleNamespace`` stand-ins. No DB.
  * Real-Postgres — drives the ``/api/recurring`` router and the ``generate_one``
    primitive against the seeded test tenants to prove CRUD + RBAC, the
    lifecycle transitions, generate-now's pre-coded approval-queue invoice, the
    DB-backed idempotency, the delete-block, and the variance hook.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.recurring_invoice import (
    CADENCE_ANNUAL,
    CADENCE_MONTHLY,
    CADENCE_QUARTERLY,
    STATUS_ACTIVE,
    RecurringInvoiceTemplate,
)
from app.models.vendor import Vendor
from app.models.workflow import AuditLog
from app.services import recurring_invoices as svc

# --------------------------------------------------------------------------- #
# Pure — period_key_for
# --------------------------------------------------------------------------- #


def test_period_key_monthly():
    assert svc.period_key_for(CADENCE_MONTHLY, date(2026, 6, 15)) == "2026-06"
    assert svc.period_key_for(CADENCE_MONTHLY, date(2026, 1, 1)) == "2026-01"


def test_period_key_quarterly():
    assert svc.period_key_for(CADENCE_QUARTERLY, date(2026, 1, 5)) == "2026-Q1"
    assert svc.period_key_for(CADENCE_QUARTERLY, date(2026, 4, 1)) == "2026-Q2"
    assert svc.period_key_for(CADENCE_QUARTERLY, date(2026, 7, 1)) == "2026-Q3"
    assert svc.period_key_for(CADENCE_QUARTERLY, date(2026, 12, 31)) == "2026-Q4"


def test_period_key_annual():
    assert svc.period_key_for(CADENCE_ANNUAL, date(2026, 9, 9)) == "2026"


# --------------------------------------------------------------------------- #
# Pure — compute_next_run_on
# --------------------------------------------------------------------------- #


def test_next_run_on_monthly_on_start():
    assert svc.compute_next_run_on(
        CADENCE_MONTHLY, 15, after=date(2026, 1, 15), start_date=date(2026, 1, 15)
    ) == date(2026, 1, 15)


def test_next_run_on_monthly_rolls_to_next_month():
    assert svc.compute_next_run_on(
        CADENCE_MONTHLY, 15, after=date(2026, 1, 20), start_date=date(2026, 1, 15)
    ) == date(2026, 2, 15)


def test_next_run_on_monthly_year_rollover():
    assert svc.compute_next_run_on(
        CADENCE_MONTHLY, 1, after=date(2026, 12, 5), start_date=date(2026, 1, 1)
    ) == date(2027, 1, 1)


def test_next_run_on_quarterly_rollover():
    # start Q1, asking after Feb → next quarter Apr.
    assert svc.compute_next_run_on(
        CADENCE_QUARTERLY, 1, after=date(2026, 2, 1), start_date=date(2026, 1, 1)
    ) == date(2026, 4, 1)


def test_next_run_on_annual_rollover():
    assert svc.compute_next_run_on(
        CADENCE_ANNUAL, 1, after=date(2026, 6, 1), start_date=date(2026, 1, 1)
    ) == date(2027, 1, 1)


def test_next_run_on_floors_to_start_date():
    # asking "after" a date BEFORE start_date → first occurrence is start period.
    assert svc.compute_next_run_on(
        CADENCE_MONTHLY, 10, after=date(2025, 1, 1), start_date=date(2026, 3, 10)
    ) == date(2026, 3, 10)


def test_next_run_on_past_end_date_is_none():
    assert (
        svc.compute_next_run_on(
            CADENCE_MONTHLY,
            1,
            after=date(2026, 5, 1),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 1),
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Pure — variance flag
# --------------------------------------------------------------------------- #


def _tmpl(**kw):
    base = dict(
        name="Acme Rent",
        amount=Decimal("1000.00"),
        variance_tolerance_pct=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _inv(amount):
    return SimpleNamespace(amount=Decimal(amount))


def test_variance_within_tolerance_returns_none():
    # default 10% tolerance; 5% drift → no flag.
    assert svc.flag_template_variance(_inv("1050.00"), _tmpl()) is None


def test_variance_over_tolerance_flags():
    flag = svc.flag_template_variance(_inv("1200.00"), _tmpl())
    assert flag is not None
    assert flag["type"] == "recurring_variance"
    assert flag["severity"] == "warning"


def test_variance_respects_template_override():
    # 5% drift but a strict 2% override → flag.
    flag = svc.flag_template_variance(_inv("1050.00"), _tmpl(variance_tolerance_pct=Decimal("2.0")))
    assert flag is not None


def test_variance_zero_template_amount_is_safe():
    assert svc.flag_template_variance(_inv("100.00"), _tmpl(amount=Decimal("0"))) is None


# --------------------------------------------------------------------------- #
# Real-DB helpers
# --------------------------------------------------------------------------- #


async def _default_entity_id(s):
    return (
        await s.execute(select(Entity.id).where(Entity.is_default.is_(True)).limit(1))
    ).scalar_one()


async def _add_vendor(mk, org_id, name="Acme Towers") -> str:
    async with mk() as s:
        v = Vendor(organization_id=org_id, name=name, entity_id=await _default_entity_id(s))
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return str(v.id)


def _create_body(**over):
    body = {
        "name": "Acme Towers — monthly rent",
        "amount": 1000.0,
        "currency": "USD",
        "cadence": "monthly",
        "day_of_period": 1,
        "start_date": date.today().replace(day=1).isoformat(),
        "gl_account": "6000",
    }
    body.update(over)
    return body


# --------------------------------------------------------------------------- #
# Real-DB — CRUD + RBAC
# --------------------------------------------------------------------------- #


async def test_create_template_sets_next_run_on_and_audits(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/recurring",
            json=_create_body(vendor_id=vendor_id, amount=2500.0),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["amount"] == 2500.0
    assert body["vendor_name"] == "Acme Towers"  # resolved from vendor_id
    assert body["next_run_on"] is not None

    async with mk() as s:
        audit = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "recurring_template.created",
                    AuditLog.entity_id == uuid.UUID(body["id"]),
                )
            )
        ).scalar_one()
        assert audit.entity_type == "recurring_invoice_template"


async def test_ap_clerk_can_read_cannot_create(realdb):
    # clerk can list (read role)
    async with realdb.client(key="a", role="ap_clerk") as c:
        listing = await c.get("/api/recurring")
        assert listing.status_code == 200, listing.text
        # clerk cannot create (write role)
        denied = await c.post("/api/recurring", json=_create_body())
        assert denied.status_code == 403


async def test_patch_recomputes_next_run_on_and_audits_changed(realdb):
    mk = realdb.sessionmaker("a")

    async with realdb.client(key="a", role="ap_manager") as c:
        tid = (await c.post("/api/recurring", json=_create_body(day_of_period=1))).json()["id"]
        before = (await c.get(f"/api/recurring/{tid}")).json()["next_run_on"]
        resp = await c.patch(f"/api/recurring/{tid}", json={"day_of_period": 15})
    assert resp.status_code == 200, resp.text
    after = resp.json()["next_run_on"]
    assert after != before
    assert after.endswith("-15")

    async with mk() as s:
        audit = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "recurring_template.updated",
                    AuditLog.entity_id == uuid.UUID(tid),
                )
            )
        ).scalar_one()
        assert "day_of_period" in (audit.details or {}).get("changed", [])


# --------------------------------------------------------------------------- #
# Real-DB — lifecycle
# --------------------------------------------------------------------------- #


async def test_lifecycle_pause_resume_end(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        tid = (await c.post("/api/recurring", json=_create_body())).json()["id"]

        paused = await c.post(f"/api/recurring/{tid}/pause")
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"

        # pausing again is invalid → 409
        assert (await c.post(f"/api/recurring/{tid}/pause")).status_code == 409

        resumed = await c.post(f"/api/recurring/{tid}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "active"
        assert resumed.json()["next_run_on"] is not None

        # resuming an active one is invalid → 409
        assert (await c.post(f"/api/recurring/{tid}/resume")).status_code == 409

        ended = await c.post(f"/api/recurring/{tid}/end")
        assert ended.status_code == 200
        assert ended.json()["status"] == "ended"
        assert ended.json()["next_run_on"] is None

        # ending again → 409
        assert (await c.post(f"/api/recurring/{tid}/end")).status_code == 409


# --------------------------------------------------------------------------- #
# Real-DB — generate-now (queue + linkage + idempotency)
# --------------------------------------------------------------------------- #


async def test_generate_now_creates_precoded_review_invoice(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        tid = (
            await c.post(
                "/api/recurring",
                json=_create_body(vendor_id=vendor_id, amount=1234.56, gl_account="7100"),
            )
        ).json()["id"]
        resp = await c.post(f"/api/recurring/{tid}/generate-now")
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    invoice_id = uuid.UUID(payload["invoice_id"])
    assert payload["status"] == "ready_for_review"

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
        assert inv.status == InvoiceStatus.ready_for_review  # approval queue, NOT pending
        assert inv.recurring_template_id == uuid.UUID(tid)
        assert inv.recurring_period_key == payload["period_key"]
        assert inv.amount == Decimal("1234.56")  # exact Numeric, not float
        assert inv.gl_account == "7100"  # pre-coded from template
        # invoice.created audit row written
        created = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "invoice.created",
                    AuditLog.entity_id == invoice_id,
                )
            )
        ).scalar_one()
        assert (created.details or {}).get("source") == "recurring_template"


async def test_generate_now_is_idempotent_for_same_period(realdb):
    mk = realdb.sessionmaker("a")
    vendor_id = await _add_vendor(mk, realdb.info("a").org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        tid = (await c.post("/api/recurring", json=_create_body(vendor_id=vendor_id))).json()["id"]
        first = await c.post(f"/api/recurring/{tid}/generate-now")
        assert first.status_code == 201, first.text
        first_id = first.json()["invoice_id"]
        period_key = first.json()["period_key"]

        # Re-call for the SAME period → 200, same invoice id, no duplicate.
        second = await c.post(f"/api/recurring/{tid}/generate-now")
        assert second.status_code == 200, second.text
        assert second.json()["invoice_id"] == first_id

    async with mk() as s:
        count = (
            (
                await s.execute(
                    select(Invoice).where(
                        Invoice.recurring_template_id == uuid.UUID(tid),
                        Invoice.recurring_period_key == period_key,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 1  # exactly ONE invoice for the period


async def test_delete_blocked_once_invoices_generated(realdb):
    mk = realdb.sessionmaker("a")
    vendor_id = await _add_vendor(mk, realdb.info("a").org_id)
    async with realdb.client(key="a", role="ap_manager") as c:
        # No invoices yet → delete succeeds.
        tid_empty = (await c.post("/api/recurring", json=_create_body())).json()["id"]
        assert (await c.delete(f"/api/recurring/{tid_empty}")).status_code == 204

        # Generate one → delete now blocked with 409.
        tid = (await c.post("/api/recurring", json=_create_body(vendor_id=vendor_id))).json()["id"]
        await c.post(f"/api/recurring/{tid}/generate-now")
        blocked = await c.delete(f"/api/recurring/{tid}")
        assert blocked.status_code == 409


# --------------------------------------------------------------------------- #
# Real-DB — upcoming schedule + history
# --------------------------------------------------------------------------- #


async def test_upcoming_schedule_projects_without_creating(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        tid = (await c.post("/api/recurring", json=_create_body(cadence="monthly"))).json()["id"]
        sched = await c.get(f"/api/recurring/{tid}/upcoming-schedule?count=4")
    assert sched.status_code == 200, sched.text
    occ = sched.json()["occurrences"]
    assert len(occ) == 4
    # period keys strictly increasing, none created in the DB
    keys = [o["period_key"] for o in occ]
    assert keys == sorted(set(keys))

    async with mk() as s:
        any_inv = (
            (
                await s.execute(
                    select(Invoice).where(Invoice.recurring_template_id == uuid.UUID(tid))
                )
            )
            .scalars()
            .all()
        )
        assert any_inv == []


# --------------------------------------------------------------------------- #
# Real-DB — variance hook on an arrived (non-generated) invoice
# --------------------------------------------------------------------------- #


async def test_variance_hook_flags_over_tolerance_arrived_invoice(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id, name="SaaS Co")

    # Active template expecting 1000 from this vendor, 10% default tolerance.
    async with mk() as s:
        t = RecurringInvoiceTemplate(
            organization_id=org_id,
            entity_id=await _default_entity_id(s),
            name="SaaS seats",
            vendor_id=uuid.UUID(vendor_id),
            vendor_name="SaaS Co",
            amount=Decimal("1000.00"),
            currency="USD",
            cadence=CADENCE_MONTHLY,
            day_of_period=1,
            start_date=date.today().replace(day=1),
            status=STATUS_ACTIVE,
        )
        s.add(t)
        await s.commit()

    # A normally-ingested invoice (NOT template-generated) from the same vendor,
    # 30% over the template amount → refresh_warnings should attach the flag.
    from app.services.invoice_warnings import refresh_warnings

    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            entity_id=await _default_entity_id(s),
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_name="SaaS Co",
            vendor_id=uuid.UUID(vendor_id),
            amount=Decimal("1300.00"),
            currency="USD",
            status=InvoiceStatus.ready_for_review,
        )
        s.add(inv)
        await s.flush()
        warnings = await refresh_warnings(s, inv)
        await s.commit()

    assert any(w["type"] == "recurring_variance" for w in warnings)
