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

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.recurring_invoice import (
    CADENCE_ANNUAL,
    CADENCE_MONTHLY,
    CADENCE_QUARTERLY,
    STATUS_ACTIVE,
    STATUS_PAUSED,
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
# Pure — current_due_run_on (the generate-now target period)
# --------------------------------------------------------------------------- #


def test_current_due_run_on_never_precedes_start_date_issue_179():
    """Issue #179: day_of_period=5 on a template started 2024-01-15 must not
    target 2024-01-05 (before the schedule even began) — the first real
    occurrence is 2024-02-05, matching what the background sweep's
    compute_next_run_on would pick as the template's first period."""
    result = svc.current_due_run_on(
        CADENCE_MONTHLY, 5, today=date(2024, 1, 20), start_date=date(2024, 1, 15)
    )
    assert result == date(2024, 2, 5)
    assert result >= date(2024, 1, 15)

    # Cross-check against the sweep's own scheduler for the same template —
    # generate-now and the sweep must agree on the first period.
    swept_first = svc.compute_next_run_on(
        CADENCE_MONTHLY, 5, after=date(2024, 1, 15), start_date=date(2024, 1, 15)
    )
    assert swept_first == date(2024, 2, 5) == result


def test_current_due_run_on_normal_case_unaffected():
    """day_of_period >= start_date.day: the first occurrence IS the start
    month's day_of_period, unchanged by the fix."""
    assert svc.current_due_run_on(
        CADENCE_MONTHLY, 20, today=date(2024, 1, 25), start_date=date(2024, 1, 15)
    ) == date(2024, 1, 20)


def test_current_due_run_on_before_schedule_starts_returns_first_occurrence():
    """today before start_date: the floor is start_date, so the (possibly
    advanced) first occurrence is returned regardless of today."""
    assert svc.current_due_run_on(
        CADENCE_MONTHLY, 5, today=date(2023, 6, 1), start_date=date(2024, 1, 15)
    ) == date(2024, 2, 5)


def test_current_due_run_on_advances_multiple_periods():
    """A day_of_period earlier than start_date.day, with `today` well past
    several periods — the latest due occurrence on/before today, never one
    before start_date."""
    assert svc.current_due_run_on(
        CADENCE_MONTHLY, 5, today=date(2024, 5, 10), start_date=date(2024, 1, 15)
    ) == date(2024, 5, 5)


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


async def test_generate_one_survives_a_racing_generation_for_the_same_period(realdb):
    """The `(template, period)` savepoint is the RACE backstop — the route's own
    pre-check means the sequential retry never reaches it, so this drives
    `generate_one` directly with the slot claimed underneath it.

    It regressed on the same SQLAlchemy trap as the card-issuance savepoint:
    `db.add(invoice)` sat BEFORE the `begin_nested()` block, and
    `SessionTransaction._take_snapshot` flushes when that boundary opens — so
    the INSERT went out before the SAVEPOINT existed. The IntegrityError still
    reached the `except`, but the transaction was already poisoned, so the
    recovery SELECT raised PendingRollbackError. In the background sweep that
    aborted the whole tenant tick, discarding every sibling template it had
    already generated.
    """
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id, name="Race Towers")
    run_on = date.today().replace(day=1)

    async with mk() as s:
        tpl = RecurringInvoiceTemplate(
            organization_id=org_id,
            entity_id=await _default_entity_id(s),
            name="Race Towers — monthly",
            vendor_id=uuid.UUID(vendor_id),
            vendor_name="Race Towers",
            amount=Decimal("1000.00"),
            currency="USD",
            cadence=CADENCE_MONTHLY,
            day_of_period=1,
            start_date=run_on,
            status=STATUS_ACTIVE,
            gl_account="6000",
        )
        s.add(tpl)
        await s.commit()
        tpl_id = tpl.id

    # A competing writer claims the period first, on its own connection.
    winner_mk = realdb.sessionmaker("a")
    async with winner_mk() as other:
        tpl_other = (
            await other.execute(
                select(RecurringInvoiceTemplate).where(RecurringInvoiceTemplate.id == tpl_id)
            )
        ).scalar_one()
        winner = await svc.generate_one(other, tpl_other, run_on=run_on)
        await other.commit()
        winner_id = winner.id

    async with mk() as db:
        tpl_ours = (
            await db.execute(
                select(RecurringInvoiceTemplate).where(RecurringInvoiceTemplate.id == tpl_id)
            )
        ).scalar_one()
        got = await svc.generate_one(db, tpl_ours, run_on=run_on)
        # Converged on the winner's invoice, and the session is still usable —
        # the sweep can go on to its next template and commit.
        assert got is not None
        assert got.id == winner_id
        await db.commit()

    period_key = svc.period_key_for(CADENCE_MONTHLY, run_on)
    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(Invoice).where(
                        Invoice.recurring_template_id == tpl_id,
                        Invoice.recurring_period_key == period_key,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


# --------------------------------------------------------------------------- #
# Real-DB — background sweep's `generated` metric (issue #152)
# --------------------------------------------------------------------------- #
#
# `generate_one()` returns the SAME non-None Invoice whether it just created
# one or hit the (template, period_key) idempotency guard and handed back the
# pre-existing row — so the sweep must not count "invoice is not None" alone,
# or a tick that generates nothing new still reports the count of no-ops it
# skipped.

_SWEEP_TODAY = date(2026, 3, 1)


async def _add_recurring_template(mk, org_id, *, next_run_on, name="Sweep Co"):
    async with mk() as s:
        t = RecurringInvoiceTemplate(
            organization_id=org_id,
            entity_id=await _default_entity_id(s),
            name=name,
            vendor_name=name,
            amount=Decimal("500.00"),
            currency="USD",
            cadence=CADENCE_MONTHLY,
            day_of_period=1,
            start_date=_SWEEP_TODAY.replace(day=1),
            status=STATUS_ACTIVE,
            next_run_on=next_run_on,
        )
        s.add(t)
        await s.commit()
        await s.refresh(t)
        return t.id


async def test_sweep_generated_counts_only_genuine_new_invoices(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    t1 = await _add_recurring_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Sweep Co 1")
    t2 = await _add_recurring_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Sweep Co 2")

    outcome = await svc._sweep_tenant(db_name, _SWEEP_TODAY)
    assert outcome.generated == 2  # two genuinely new invoices, one per template
    assert outcome.templates_skipped == 0
    assert outcome.template_failures == 0

    async with mk() as s:
        rows = (
            (await s.execute(select(Invoice).where(Invoice.recurring_template_id.in_([t1, t2]))))
            .scalars()
            .all()
        )
        assert len(rows) == 2


async def test_sweep_reports_zero_when_period_already_generated(realdb):
    """Issue #152: a tick that generates nothing new must report 0, not the
    count of already-generated periods it skipped."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    tid = await _add_recurring_template(mk, org_id, next_run_on=_SWEEP_TODAY)

    first = await svc._sweep_tenant(db_name, _SWEEP_TODAY)
    assert first.generated == 1  # the one genuine create

    # Simulate the cursor sitting back on an already-generated period (e.g. a
    # stuck/retried cursor) so this tick's only work is the idempotent no-op.
    async with mk() as s:
        t = (
            await s.execute(
                select(RecurringInvoiceTemplate).where(RecurringInvoiceTemplate.id == tid)
            )
        ).scalar_one()
        t.next_run_on = _SWEEP_TODAY
        await s.commit()

    second = await svc._sweep_tenant(db_name, _SWEEP_TODAY)
    assert second.generated == 0  # idempotent no-op — NOT the count of skipped periods

    async with mk() as s:
        rows = (
            (await s.execute(select(Invoice).where(Invoice.recurring_template_id == tid)))
            .scalars()
            .all()
        )
        assert len(rows) == 1  # still exactly one invoice — no duplicate


# --------------------------------------------------------------------------- #
# Real-DB — a template that CANNOT generate must not skip in silence
# --------------------------------------------------------------------------- #
#
# `generate_one` returns None for a template missing `amount` or `vendor_name`,
# and the sweep's defensive cursor advance then rolls `next_run_on` forward
# anyway (correctly — a stuck cursor must not spin the sweep forever). What was
# missing is the other half: the template stayed `active`, `generated_count`
# never moved, and the only trace was a log line. Month after month a
# subscription invoice a tenant believes is being raised into the approval queue
# simply wasn't, and `GET /api/recurring/{id}/history` showed an empty run
# history indistinguishable from "nothing due yet".


async def _add_ungeneratable_template(mk, org_id, *, next_run_on, name="No Amount Co", **over):
    fields = {
        "vendor_name": name,
        "amount": None,  # <- not generatable
    }
    fields.update(over)
    async with mk() as s:
        t = RecurringInvoiceTemplate(
            organization_id=org_id,
            entity_id=await _default_entity_id(s),
            name=name,
            currency="USD",
            cadence=CADENCE_MONTHLY,
            day_of_period=1,
            start_date=_SWEEP_TODAY.replace(day=1),
            status=STATUS_ACTIVE,
            next_run_on=next_run_on,
            **fields,
        )
        s.add(t)
        await s.commit()
        await s.refresh(t)
        return t.id


async def _reload(mk, template_id):
    async with mk() as s:
        return (
            await s.execute(
                select(RecurringInvoiceTemplate).where(RecurringInvoiceTemplate.id == template_id)
            )
        ).scalar_one()


def test_not_generatable_reason_names_what_is_missing():
    """Pure — the single condition `generate_one`, the sweep and the router's
    422 all read, so they can't drift apart."""
    ok = SimpleNamespace(amount=Decimal("10.00"), vendor_name="Acme")
    assert svc.not_generatable_reason(ok) is None
    assert (
        svc.not_generatable_reason(SimpleNamespace(amount=None, vendor_name="Acme"))
        == svc.SKIP_MISSING_AMOUNT
    )
    assert (
        svc.not_generatable_reason(SimpleNamespace(amount=Decimal("10.00"), vendor_name=""))
        == svc.SKIP_MISSING_VENDOR
    )
    assert (
        svc.not_generatable_reason(SimpleNamespace(amount=None, vendor_name=None))
        == svc.SKIP_MISSING_AMOUNT_AND_VENDOR
    )


async def test_sweep_persists_a_reason_when_a_template_cannot_generate(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    tid = await _add_ungeneratable_template(mk, org_id, next_run_on=_SWEEP_TODAY)

    outcome = await svc._sweep_tenant(db_name, _SWEEP_TODAY)
    assert outcome.generated == 0
    assert outcome.templates_skipped == 1

    template = await _reload(mk, tid)
    marker = (template.meta or {}).get(svc.SKIP_META_KEY)
    assert marker, "the skip must be PERSISTED, not only logged"
    assert marker["reason"] == svc.SKIP_MISSING_AMOUNT
    assert marker["period_key"] == svc.period_key_for(CADENCE_MONTHLY, _SWEEP_TODAY)
    assert marker["consecutive"] == 1
    assert marker["last_skipped_at"]

    # ...and it lands on the append-only trail, PII-free.
    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "recurring_template.generation_skipped",
                        AuditLog.entity_id == tid,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].details["reason"] == svc.SKIP_MISSING_AMOUNT
    assert rows[0].details["consecutive"] == 1
    # The cursor still advances — a template that can't generate must not spin
    # the sweep forever.
    assert template.next_run_on > _SWEEP_TODAY


async def test_sweep_pauses_a_template_after_repeated_non_generatable_periods(realdb):
    """The skip counter is bounded: past `MAX_CONSECUTIVE_SKIPS` the template is
    paused and audited, so it stops pretending to be an active schedule."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    tid = await _add_ungeneratable_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Pause Co")

    day = _SWEEP_TODAY
    for _ in range(svc.MAX_CONSECUTIVE_SKIPS):
        await svc._sweep_tenant(db_name, day)
        day = svc._add_months(day, 1)

    template = await _reload(mk, tid)
    assert template.status == STATUS_PAUSED
    assert (template.meta or {})[svc.SKIP_META_KEY]["consecutive"] == svc.MAX_CONSECUTIVE_SKIPS

    async with mk() as s:
        paused = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "recurring_template.paused",
                        AuditLog.entity_id == tid,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(paused) == 1
    assert paused[0].actor_id is None  # the sweep, not a human
    assert paused[0].details["reason"] == svc.SKIP_MISSING_AMOUNT

    # Paused means paused — a further tick doesn't touch it again.
    after = await svc._sweep_tenant(db_name, day)
    assert after.templates_skipped == 0


async def test_sweep_clears_the_skip_marker_once_the_template_generates(realdb):
    """`consecutive` counts CONSECUTIVE skips — a fixed template starts over."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    tid = await _add_ungeneratable_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Fixed Co")
    await svc._sweep_tenant(db_name, _SWEEP_TODAY)
    assert (await _reload(mk, tid)).meta[svc.SKIP_META_KEY]["consecutive"] == 1

    # An operator fills in the missing amount and re-arms the cursor.
    next_month = svc._add_months(_SWEEP_TODAY, 1)
    async with mk() as s:
        t = (
            await s.execute(
                select(RecurringInvoiceTemplate).where(RecurringInvoiceTemplate.id == tid)
            )
        ).scalar_one()
        t.amount = Decimal("250.00")
        t.next_run_on = next_month
        await s.commit()

    outcome = await svc._sweep_tenant(db_name, next_month)
    assert outcome.generated == 1

    template = await _reload(mk, tid)
    assert svc.SKIP_META_KEY not in (template.meta or {})


async def test_one_failing_template_does_not_discard_its_siblings_work(realdb):
    """Per-template commit: `_sweep_tenant` used to run every template in one
    transaction and commit once at the end, so a single template that raised
    aborted the tenant's whole tick and threw away the invoices already
    generated on it. Same shape fixed in `vendor_rescreen` / `payment_erp_sync`.
    """
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    # Ordered by next_run_on, so the good one is processed first and the poison
    # one second — the ordering that used to lose the good one's invoice.
    good = await _add_recurring_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Healthy Co")
    poison = await _add_recurring_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Poison Co")

    real_generate = svc.generate_one

    async def _explode_for_poison(db, template, **kwargs):
        if template.id == poison:
            raise RuntimeError("boom")
        return await real_generate(db, template, **kwargs)

    with patch.object(svc, "generate_one", _explode_for_poison):
        outcome = await svc._sweep_tenant(db_name, _SWEEP_TODAY)

    assert outcome.template_failures == 1
    assert outcome.generated == 1, "the healthy template's invoice must survive"

    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(Invoice).where(Invoice.recurring_template_id.in_([good, poison]))
                )
            )
            .scalars()
            .all()
        )
    assert [r.recurring_template_id for r in rows] == [good]


async def test_sweep_logs_exception_class_not_message_for_a_failing_template(realdb, caplog):
    """PII-out-of-logs: a template's generation failure logs the exception CLASS
    only — a DB/asyncpg error string can echo a vendor name."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name
    await _add_recurring_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Loud Co")

    sentinel = "VENDOR_BANK_ACCOUNT_9876543210"

    async def _explode(db, template, **kwargs):
        raise RuntimeError(sentinel)

    with (
        patch.object(svc, "generate_one", _explode),
        caplog.at_level(logging.WARNING, logger=svc.logger.name),
    ):
        outcome = await svc._sweep_tenant(db_name, _SWEEP_TODAY)

    assert outcome.template_failures == 1
    assert caplog.records
    for record in caplog.records:
        assert sentinel not in record.getMessage()
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


async def test_api_surfaces_the_persisted_skip_so_it_is_not_invisible(realdb):
    """`GET /api/recurring/{id}` reports the skip, so "nothing raised for
    months" is distinguishable from "nothing due yet" in the UI — the thing an
    invisible log line could never do."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    tid = await _add_ungeneratable_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Silent Co")
    await svc._sweep_tenant(db_name, _SWEEP_TODAY)

    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.get(f"/api/recurring/{tid}")
        assert r.status_code == 200
        skip = r.json()["last_skip"]
    assert skip["reason"] == svc.SKIP_MISSING_AMOUNT
    assert skip["consecutive"] == 1
    assert skip["period_key"] == svc.period_key_for(CADENCE_MONTHLY, _SWEEP_TODAY)


async def test_api_last_skip_is_none_for_a_healthy_template(realdb):
    mk = realdb.sessionmaker("a")
    tid = await _add_recurring_template(
        mk, realdb.info("a").org_id, next_run_on=_SWEEP_TODAY, name="Healthy Read Co"
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.get(f"/api/recurring/{tid}")
    assert r.status_code == 200
    assert r.json()["last_skip"] is None


async def test_api_last_skip_tolerates_a_malformed_meta_marker(realdb):
    """A hand-edited / legacy `meta` must never 500 the templates list."""
    mk = realdb.sessionmaker("a")
    tid = await _add_recurring_template(
        mk, realdb.info("a").org_id, next_run_on=_SWEEP_TODAY, name="Bad Meta Co"
    )
    async with mk() as s:
        t = (
            await s.execute(
                select(RecurringInvoiceTemplate).where(RecurringInvoiceTemplate.id == tid)
            )
        ).scalar_one()
        t.meta = {svc.SKIP_META_KEY: "not-a-dict"}
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.get(f"/api/recurring/{tid}")
    assert r.status_code == 200
    assert r.json()["last_skip"] is None


# --------------------------------------------------------------------------- #
# Real-DB — the skip marker is cleared by every path that fixes the template
# --------------------------------------------------------------------------- #
#
# The marker is not cosmetic: `record_generation_skip` counts UP from whatever
# it finds, so a stale count left behind after a manual fix makes the NEXT
# single miss trip the MAX_CONSECUTIVE_SKIPS auto-pause meant for three — and a
# healthy template goes on reporting "Not generating" to the operator who just
# fixed it, which is the original bug inverted.


async def test_generate_now_clears_a_stale_skip_marker(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name
    vendor_name = "Manual Fix Co"

    tid = await _add_ungeneratable_template(mk, org_id, next_run_on=_SWEEP_TODAY, name=vendor_name)
    await svc._sweep_tenant(db_name, _SWEEP_TODAY)
    assert (await _reload(mk, tid)).meta[svc.SKIP_META_KEY]["consecutive"] == 1

    # The operator fills in the amount and generates by hand.
    async with realdb.client(key="a", role="ap_manager") as c:
        patched = await c.patch(f"/api/recurring/{tid}", json={"amount": 250.0})
        assert patched.status_code == 200
        r = await c.post(f"/api/recurring/{tid}/generate-now")
        assert r.status_code in (200, 201)

    assert svc.SKIP_META_KEY not in ((await _reload(mk, tid)).meta or {})


async def test_generate_now_clears_the_marker_on_the_idempotent_branch(realdb):
    """The already-generated 200 never reaches `generate_one`'s own clear."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id

    tid = await _add_recurring_template(
        mk, org_id, next_run_on=date.today(), name="Idempotent Fix Co"
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        first = await c.post(f"/api/recurring/{tid}/generate-now")
        assert first.status_code == 201

        # Plant a stale marker, then re-call: the period is already satisfied,
        # so the router short-circuits to 200 before `generate_one`.
        async with mk() as s:
            t = (
                await s.execute(
                    select(RecurringInvoiceTemplate).where(RecurringInvoiceTemplate.id == tid)
                )
            ).scalar_one()
            svc.record_generation_skip(
                t, period_key="2020-01", reason=svc.SKIP_MISSING_AMOUNT, at=datetime.now(UTC)
            )
            await s.commit()

        again = await c.post(f"/api/recurring/{tid}/generate-now")
        assert again.status_code == 200

    assert svc.SKIP_META_KEY not in ((await _reload(mk, tid)).meta or {})


async def test_patch_that_fixes_the_template_clears_the_marker(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    tid = await _add_ungeneratable_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Patch Co")
    await svc._sweep_tenant(db_name, _SWEEP_TODAY)
    assert (await _reload(mk, tid)).meta[svc.SKIP_META_KEY]

    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.patch(f"/api/recurring/{tid}", json={"amount": 99.0})
    assert r.status_code == 200
    assert r.json()["last_skip"] is None
    assert svc.SKIP_META_KEY not in ((await _reload(mk, tid)).meta or {})


async def test_patch_that_does_not_fix_the_template_keeps_the_marker(realdb):
    """A still-unfixable template keeps its marker — the reason is still true."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    tid = await _add_ungeneratable_template(
        mk, org_id, next_run_on=_SWEEP_TODAY, name="Still Broken Co"
    )
    await svc._sweep_tenant(db_name, _SWEEP_TODAY)

    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.patch(f"/api/recurring/{tid}", json={"notes": "chasing the vendor"})
    assert r.status_code == 200
    assert r.json()["last_skip"]["reason"] == svc.SKIP_MISSING_AMOUNT


async def test_a_manual_generation_resets_the_auto_pause_budget(realdb):
    """The consecutive count is what the auto-pause reads, so a fix must reset
    it — otherwise the next SINGLE miss trips a pause meant for three."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    tid = await _add_ungeneratable_template(mk, org_id, next_run_on=_SWEEP_TODAY, name="Budget Co")

    day = _SWEEP_TODAY
    for _ in range(svc.MAX_CONSECUTIVE_SKIPS - 1):
        await svc._sweep_tenant(db_name, day)
        day = svc._add_months(day, 1)
    assert (await _reload(mk, tid)).meta[svc.SKIP_META_KEY]["consecutive"] == (
        svc.MAX_CONSECUTIVE_SKIPS - 1
    )

    # Operator fixes it and generates by hand...
    async with realdb.client(key="a", role="ap_manager") as c:
        assert (await c.patch(f"/api/recurring/{tid}", json={"amount": 40.0})).status_code == 200
        assert (await c.post(f"/api/recurring/{tid}/generate-now")).status_code in (200, 201)

    # ...then it regresses (amount cleared again) and misses ONE more period.
    async with mk() as s:
        t = (
            await s.execute(
                select(RecurringInvoiceTemplate).where(RecurringInvoiceTemplate.id == tid)
            )
        ).scalar_one()
        t.amount = None
        t.next_run_on = day
        t.status = STATUS_ACTIVE
        await s.commit()
    await svc._sweep_tenant(db_name, day)

    template = await _reload(mk, tid)
    assert template.meta[svc.SKIP_META_KEY]["consecutive"] == 1, "the budget must have reset"
    assert template.status == STATUS_ACTIVE, "one miss must not trip a three-miss pause"


async def test_resume_clears_the_marker_only_when_the_template_is_fixed(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    db_name = realdb.info("a").db_name

    # Auto-paused, still unfixable -> resume keeps the marker.
    broken = await _add_ungeneratable_template(
        mk, org_id, next_run_on=_SWEEP_TODAY, name="Resume Broken Co"
    )
    day = _SWEEP_TODAY
    for _ in range(svc.MAX_CONSECUTIVE_SKIPS):
        await svc._sweep_tenant(db_name, day)
        day = svc._add_months(day, 1)
    assert (await _reload(mk, broken)).status == STATUS_PAUSED

    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.post(f"/api/recurring/{broken}/resume")
        assert r.status_code == 200
        assert r.json()["last_skip"]["reason"] == svc.SKIP_MISSING_AMOUNT

        # Fix it, pause + resume -> the marker goes.
        assert (await c.patch(f"/api/recurring/{broken}", json={"amount": 12.0})).status_code == 200
    assert svc.SKIP_META_KEY not in ((await _reload(mk, broken)).meta or {})
