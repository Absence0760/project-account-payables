"""Aging buckets reconcile with the accounts-payable balance (F-4).

The aging bands (dashboard KPI, the `aging_snapshot` CSV export, and the emailed
scheduled report) and the CFO `accounts_payable_balance` used to cover disjoint
invoice populations: aging counted ``new / pending / ready_for_review /
approved`` while the AP balance counted ``approved → payment_scheduled``. Summing
the aging columns therefore never matched the AP-balance tile — pre-approval
invoices inflated aging, in-flight-to-payment invoices were missing from it.

Every aging surface now filters on the canonical ``OPEN_AP_STATUSES`` set (the
same one the AP balance uses), so the bands always sum to the AP balance. These
realdb tests pin that reconciliation on all three surfaces + the FX-outage
fallback (F-8).
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus

TENANT = "a"

# 300 (current, approved) + 1000 (31-60, approved) + 2000 (90+, payment_scheduled)
_EXPECTED_AP = Decimal("3300.00")


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed(realdb) -> Decimal:
    """One invoice per relevant status, all with due dates, under the Default
    entity. The three open-payable rows sum to ``_EXPECTED_AP``; the
    pending/paid/rejected rows must NOT count toward aging or the AP balance."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = date.today()

    def inv(num, amt, status, due_offset, ent):
        return Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=num,
            vendor_name="Recon Co",
            amount=Decimal(amt),
            currency="USD",
            status=status,
            invoice_date=today - timedelta(days=120),
            due_date=today + timedelta(days=due_offset),
        )

    async with mk() as s:
        ent = await _default_entity_id(s)
        s.add_all(
            [
                inv("AG-CUR", "300.00", InvoiceStatus.approved, 10, ent),  # current
                inv("AG-60", "1000.00", InvoiceStatus.approved, -45, ent),  # 31-60
                inv("AG-90P", "2000.00", InvoiceStatus.payment_scheduled, -100, ent),  # 90+
                inv("AG-PEND", "500.00", InvoiceStatus.pending, -10, ent),  # excluded now
                inv("AG-PAID", "700.00", InvoiceStatus.paid, -20, ent),  # excluded
                inv("AG-REJ", "9000.00", InvoiceStatus.rejected, -5, ent),  # excluded
            ]
        )
        await s.commit()
    return _EXPECTED_AP


def test_open_ap_statuses_match_enum_and_exclude_terminal():
    """The canonical tuple is stated as literals (no ORM import) — pin it to the
    enum so a status rename can't silently desync the payable population."""
    from app.services.analytics import OPEN_AP_STATUSES

    assert set(OPEN_AP_STATUSES) == {
        InvoiceStatus.approved.value,
        InvoiceStatus.sending_to_erp.value,
        InvoiceStatus.sent_to_erp.value,
        InvoiceStatus.posted_in_erp.value,
        InvoiceStatus.payment_scheduled.value,
    }
    # Pre-approval and terminal states are intentionally NOT payable.
    for excluded in (
        InvoiceStatus.new.value,
        InvoiceStatus.pending.value,
        InvoiceStatus.ready_for_review.value,
        InvoiceStatus.rejected.value,
        InvoiceStatus.paid.value,
        InvoiceStatus.done.value,
    ):
        assert excluded not in OPEN_AP_STATUSES


@pytest.mark.asyncio
async def test_dashboard_aging_sums_to_cfo_ap_balance(realdb):
    expected = await _seed(realdb)
    async with realdb.client(key=TENANT, role="cfo") as c:
        dash = (await c.get("/api/dashboard")).json()
        cfo = (await c.get("/api/analytics/cfo")).json()

    aging = dash["aging"]
    aging_total = sum(Decimal(str(v)) for v in aging.values())
    ap_balance = Decimal(str(cfo["accounts_payable_balance"]))

    # The AP balance and every aging band cover the same population now.
    assert ap_balance == expected
    assert aging_total == ap_balance
    # Correct band distribution.
    assert Decimal(str(aging["current"])) == Decimal("300")
    assert Decimal(str(aging["days_60"])) == Decimal("1000")
    assert Decimal(str(aging["days_90_plus"])) == Decimal("2000")
    # The pending $500 is gone from aging — old behaviour would have made the
    # aging total 3800 while the AP balance stayed 3300 (the F-4 gap).
    assert aging_total != Decimal("3800")


@pytest.mark.asyncio
async def test_aging_snapshot_export_sums_to_ap_balance(realdb):
    expected = await _seed(realdb)
    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.get("/api/analytics/export/aging_snapshot")
    assert resp.status_code == 200
    # The CSV export prepends a brand-provenance comment block — start the
    # DictReader at the real grid header.
    lines = resp.text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("as_of_date"))
    row = next(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    assert Decimal(row["total"]) == expected
    assert Decimal(row["days_60"]) == Decimal("1000.00")
    assert Decimal(row["days_90_plus"]) == Decimal("2000.00")


@pytest.mark.asyncio
async def test_scheduled_report_aging_excludes_pre_approval(realdb):
    """The emailed scheduled-report aging snapshot uses the same population."""
    expected = await _seed(realdb)
    from app.services.scheduled_reports import _generate_report_payload

    schedule = SimpleNamespace(report_type="aging_snapshot", period_days=30)
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        csv_text = await _generate_report_payload(s, schedule)
    row = next(csv.DictReader(io.StringIO(csv_text)))
    assert Decimal(row["total"]) == expected
    # pending's $500 excluded → not 3800.
    assert Decimal(row["total"]) != Decimal("3800.00")


@pytest.mark.asyncio
async def test_cfo_unrealized_fx_fallback_on_outage(realdb, monkeypatch):
    """On an FX-adapter outage the CFO dashboard degrades gracefully: the
    payload flags `available: False` and reports a real (Decimal-derived) zero
    rather than a bare float sentinel (F-8) — and never 500s."""
    await _seed(realdb)

    async def _boom(*args, **kwargs):
        raise RuntimeError("fx provider unreachable")

    monkeypatch.setattr("app.api.analytics.compute_unrealized_fx_gain_loss", _boom)

    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.get("/api/analytics/cfo")
    assert resp.status_code == 200
    fx = resp.json()["unrealized_fx"]
    assert fx["available"] is False
    assert Decimal(str(fx["total_unrealized_gain_loss"])) == Decimal("0")
    assert fx["by_currency"] == []
