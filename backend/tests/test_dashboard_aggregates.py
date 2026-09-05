"""`GET /api/dashboard` — the four aggregates that were each wrong in their own
way, pinned against a real Postgres tenant.

  1. `total_paid` sums the raw `Payment.amount` column, which is denominated in
     the INVOICE's currency; `total_paid_reporting` is the converted figure.
  2. `discount_capture` counted every still-OPEN discount window as already
     missed — the one consumer of these economics that never applied the
     elapsed-window gate — and summed the discount amounts across currencies.
  3. `touchless_rate` omitted `sending_to_erp` from both legs, so an invoice
     sitting in the ERP export hop vanished from a metric it had already
     earned a place in; `failed` was omitted wholesale even though half the
     invoices in it are approved ones whose ERP export failed; and `done` /
     `paid` counted as "cleared review" on status alone, even for the rows
     that reached them WITHOUT ever being approved (`new -> done`, or a CSV
     import that bypasses the workflow engine).
  4. `monthly_trend` bounded a CALENDAR-MONTH `GROUP BY` with a rolling
     180-day window — two units that never line up, so the oldest bar was a
     partial slice of a month (or a seventh, stub bucket).

Each test is written to FAIL against the previous implementation; the
docstrings say how.

DO NOT run this file standalone in a concurrent build — the ``realdb`` fixture
truncates all tables sequentially.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentSchedule
from app.services.csv_import import (
    IMPORT_PROVENANCE_KEY,
    build_import_provenance,
    import_invoices_csv,
)
from app.utils.dates import utc_today

TENANT = "a"

# EUR -> USD locked rate used throughout: 1000.00 EUR books as 1086.96 USD.
_EUR_FACE = Decimal("1000.00")
_EUR_AS_USD = Decimal("1086.96")
_USD_FACE = Decimal("1000.00")


async def _default_entity_id(s):
    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


def _inv(org_id, ent, **kw) -> Invoice:
    kw.setdefault("invoice_number", f"DA-{uuid.uuid4().hex[:8]}")
    kw.setdefault("vendor_name", "Dashboard Aggregates Co")
    kw.setdefault("amount", _USD_FACE)
    kw.setdefault("currency", "USD")
    return Invoice(organization_id=org_id, entity_id=ent, **kw)


# ---------------------------------------------------------------------------
# 1. total_paid — cross-currency SUM vs the converted counterpart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_total_paid_reporting_converts_where_the_raw_sum_mixes_currencies(realdb):
    """Two completed payments — one USD, one EUR carrying the rate-locked
    home-currency debit leg `international_payments` writes.

    `total_paid` adds 1000 USD to 1000 EUR as though they were the same unit;
    `total_paid_reporting` resolves the EUR leg to its locked 1086.96 USD. The
    two MUST differ — an implementation that reported the raw sum under the
    org's currency code fails here.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = utc_today()
    async with mk() as s:
        ent = await _default_entity_id(s)
        usd = _inv(org_id, ent, status=InvoiceStatus.paid, invoice_date=today)
        eur = _inv(
            org_id,
            ent,
            amount=_EUR_FACE,
            currency="EUR",
            status=InvoiceStatus.paid,
            invoice_date=today,
        )
        s.add_all([usd, eur])
        await s.flush()
        s.add(
            Payment(
                entity_id=ent,
                invoice_id=usd.id,
                amount=_USD_FACE,
                method="ach",
                provider="mock",
                status="completed",
                completed_at=today,
            )
        )
        s.add(
            Payment(
                entity_id=ent,
                invoice_id=eur.id,
                # Denominated in the INVOICE's currency.
                amount=_EUR_FACE,
                # The home-currency (USD) debit, locked at submission.
                source_amount=_EUR_AS_USD,
                source_currency="USD",
                method="wire",
                provider="mock",
                status="completed",
                completed_at=today,
            )
        )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert Decimal(str(body["total_paid"])) == _USD_FACE + _EUR_FACE
    assert Decimal(str(body["total_paid_reporting"])) == _USD_FACE + _EUR_AS_USD
    assert body["total_paid_reporting"] != body["total_paid"]
    assert body["total_paid_unconverted_count"] == 0


# ---------------------------------------------------------------------------
# 2. discount_capture — the elapsed-window gate, and the currency
# ---------------------------------------------------------------------------


async def _seed_discount_invoice(
    realdb,
    *,
    discount_offset_days: int,
    paid: bool,
    currency: str = "USD",
    amount: Decimal = _USD_FACE,
    reporting_amount: Decimal | None = None,
) -> None:
    """One invoice on 10%-early-pay terms whose discount deadline is
    `discount_offset_days` from today (negative = elapsed)."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = utc_today()
    async with mk() as s:
        ent = await _default_entity_id(s)
        kw = {}
        if reporting_amount is not None:
            kw = {"reporting_currency": "USD", "reporting_amount": reporting_amount}
        inv = _inv(
            org_id,
            ent,
            amount=amount,
            currency=currency,
            status=InvoiceStatus.approved,
            invoice_date=today - timedelta(days=30),
            **kw,
        )
        s.add(inv)
        await s.flush()
        ddate = today + timedelta(days=discount_offset_days)
        s.add(
            PaymentSchedule(
                invoice_id=inv.id,
                due_date=today + timedelta(days=45),
                discount_date=ddate,
                discount_percent=Decimal("10.00"),
            )
        )
        if paid:
            s.add(
                Payment(
                    entity_id=ent,
                    invoice_id=inv.id,
                    amount=amount,
                    method="ach",
                    provider="mock",
                    status="completed",
                    # Paid inside the window.
                    completed_at=ddate - timedelta(days=1),
                )
            )
        await s.commit()


@pytest.mark.asyncio
async def test_discount_capture_open_window_is_pending_not_missed(realdb):
    """A discount deadline 10 days in the FUTURE, unpaid.

    The old fold had exactly two buckets — captured and everything-else — so
    this row landed in `missed_count` / `missed_amount` and dragged
    `capture_rate_pct` to 0%: the dashboard reported forgone savings that were
    still fully on the table, and every newly-scheduled discount joined the
    pile the moment it was written. Pre-fix this asserts
    `missed_count == 1`; post-fix the row is `pending`.
    """
    await _seed_discount_invoice(realdb, discount_offset_days=10, paid=False)
    async with realdb.client(key=TENANT, role="admin") as c:
        d = (await c.get("/api/dashboard")).json()["discount_capture"]

    assert d["eligible_count"] == 1
    assert d["missed_count"] == 0
    assert Decimal(str(d["missed_amount"])) == Decimal("0.00")
    assert d["pending_count"] == 1
    assert Decimal(str(d["pending_amount"])) == Decimal("100.00")
    # Nothing decided yet — a 0% capture rate would read as "we captured none
    # of the discounts we could have", the opposite of the truth.
    assert d["capture_rate_pct"] is None
    assert d["insufficient_data"] is True


@pytest.mark.asyncio
async def test_discount_capture_elapsed_window_is_still_a_miss(realdb):
    """The gate must not swallow real misses: a deadline that passed 10 days
    ago with no payment IS forgone savings."""
    await _seed_discount_invoice(realdb, discount_offset_days=-10, paid=False)
    async with realdb.client(key=TENANT, role="admin") as c:
        d = (await c.get("/api/dashboard")).json()["discount_capture"]

    assert d["missed_count"] == 1
    assert d["pending_count"] == 0
    assert Decimal(str(d["missed_amount"])) == Decimal("100.00")
    assert d["capture_rate_pct"] == 0.0
    assert d["insufficient_data"] is False


@pytest.mark.asyncio
async def test_discount_capture_amounts_are_in_the_reporting_currency(realdb):
    """A EUR invoice with a rate-locked `reporting_amount`, captured.

    The discount is a percentage of the invoice, so summing 10% of the FACE
    EUR amount into a figure labelled with the org's currency is the same
    cross-currency SUM the rest of this page already fixed. `captured_amount`
    keeps the face figure (100.00 EUR); `captured_amount_reporting` is 10% of
    the locked 1086.96 USD. Pre-fix there was no reporting field at all.
    """
    await _seed_discount_invoice(
        realdb,
        discount_offset_days=-5,
        paid=True,
        currency="EUR",
        amount=_EUR_FACE,
        reporting_amount=_EUR_AS_USD,
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        d = (await c.get("/api/dashboard")).json()["discount_capture"]

    assert d["captured_count"] == 1
    assert Decimal(str(d["captured_amount"])) == Decimal("100.00")
    assert Decimal(str(d["captured_amount_reporting"])) == Decimal("108.70")
    assert d["reporting_currency"] == "USD"
    assert d["unconverted_count"] == 0


@pytest.mark.asyncio
async def test_discount_capture_unconvertible_row_is_disclosed(realdb):
    """A EUR invoice with NO rate lock falls back to face value — which is the
    right call for a spend figure (dropping it would understate) but must be
    ON SCREEN, not silent (`docs/decisions.md` §35)."""
    await _seed_discount_invoice(
        realdb,
        discount_offset_days=-5,
        paid=True,
        currency="EUR",
        amount=_EUR_FACE,
        reporting_amount=None,
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        d = (await c.get("/api/dashboard")).json()["discount_capture"]

    assert d["captured_count"] == 1
    assert d["unconverted_count"] == 1


# ---------------------------------------------------------------------------
# 3. touchless_rate
# ---------------------------------------------------------------------------


async def _seed_statuses(realdb, rows: list[tuple]) -> None:
    """`rows` is [(status, has_approval_stamp)], optionally with a third
    element: a `meta` dict to persist on the row (the import provenance
    marker, or another `meta` tenant such as a cached audit summary)."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = utc_today()
    async with mk() as s:
        ent = await _default_entity_id(s)
        for row in rows:
            status, approved = row[0], row[1]
            extra = {"meta": row[2]} if len(row) > 2 else {}
            # `meta` is OMITTED, not passed as None, when no dict is supplied:
            # SQLAlchemy's JSON type persists a Python `None` as JSON `null`,
            # which is a different value from SQL NULL and behaves differently
            # under the `?` operator. Omitting the column is what real invoices
            # that never touch `meta` produce.
            s.add(
                _inv(
                    org_id,
                    ent,
                    status=status,
                    invoice_date=today,
                    approval_date=today if approved else None,
                    approved_by="Reviewer" if approved else None,
                    **extra,
                )
            )
        await s.commit()


def _import_marker() -> dict:
    return {IMPORT_PROVENANCE_KEY: build_import_provenance()}


async def _import_invoices(realdb, csv_text: str) -> None:
    """Run the REAL importer against the tenant DB, so these tests pin the
    end-to-end wiring (importer stamps -> dashboard excludes) rather than a
    hand-written restatement of the marker."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        ent = await _default_entity_id(s)
        result = await import_invoices_csv(s, org_id, csv_text, entity_id=ent)
        assert not result.errors, result.to_dict()
        await s.commit()


@pytest.mark.asyncio
async def test_touchless_rate_counts_invoices_in_the_erp_export_hop(realdb):
    """`sending_to_erp` is reachable ONLY from `approved`, so an invoice there
    has provably cleared review — yet it appeared in NEITHER leg.

    One `sending_to_erp` + one `rejected`. Pre-fix the numerator was 0 and the
    denominator 1 → 0.0%. Post-fix it is 1/2 → 50.0%.
    """
    await _seed_statuses(
        realdb,
        [(InvoiceStatus.sending_to_erp, True), (InvoiceStatus.rejected, False)],
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert body["pipeline"]["sending_to_erp"] == 1
    assert body["touchless_rate"] == 50.0


@pytest.mark.asyncio
async def test_touchless_rate_counts_a_failed_invoice_that_had_been_approved(realdb):
    """`failed` is reachable from `sending_to_erp` (approved, then the ERP
    export blew up). That invoice cleared review; it belongs in both legs.

    One such `failed` invoice (carrying the durable `approval_date` stamp) plus
    one `rejected`. Pre-fix: 0/1 → 0.0%. Post-fix: 1/2 → 50.0%.
    """
    await _seed_statuses(
        realdb,
        [(InvoiceStatus.failed, True), (InvoiceStatus.rejected, False)],
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert body["touchless_rate"] == 50.0


@pytest.mark.asyncio
async def test_touchless_rate_ignores_a_failed_invoice_that_never_reached_review(realdb):
    """The other `failed` edge: `pending -> failed` is an EXTRACTION failure.
    That invoice never finished review, so it is evidence neither for nor
    against touchless processing and must be in neither leg — counting it as
    cleared would inflate the board metric off invoices nobody ever approved.

    One extraction-failed invoice (no approval stamp) + one approved. The rate
    stays 100.0% rather than dropping to 50.0%.
    """
    await _seed_statuses(
        realdb,
        [(InvoiceStatus.failed, False), (InvoiceStatus.approved, True)],
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert body["touchless_rate"] == 100.0


@pytest.mark.asyncio
async def test_touchless_rate_excludes_a_done_invoice_that_skipped_approval(realdb):
    """`new -> done` is a legal transition that skips approval outright (and
    the Day-0 CSV importer lands historical rows there by default). Such an
    invoice never cleared review — it bypassed it — so counting it as
    touchless inflates a board-reported automation figure.

    One shortcut `done` (no approval stamp) + one rejected. Pre-fix the `done`
    row counted as cleared and the rate read 50.0%; it is now out of the
    population entirely, and with nothing left that cleared review the honest
    answer is 0.0%.
    """
    await _seed_statuses(
        realdb,
        [(InvoiceStatus.done, False), (InvoiceStatus.rejected, False)],
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert body["pipeline"]["done"] == 1
    assert body["touchless_rate"] == 0.0


@pytest.mark.asyncio
async def test_touchless_rate_counts_a_done_invoice_that_cleared_a_real_approval(realdb):
    """The other `done`: approved (untouched) and carried through to terminal.
    It has the durable `approval_date` stamp, so it counts exactly as before —
    the change narrows the numerator, it does not gut it."""
    await _seed_statuses(
        realdb,
        [(InvoiceStatus.done, True), (InvoiceStatus.rejected, False)],
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert body["touchless_rate"] == 50.0


@pytest.mark.asyncio
async def test_touchless_rate_excludes_an_imported_paid_invoice(realdb):
    """`services/csv_import` may also land a historical row straight at `paid`,
    bypassing the workflow engine — the same hole as `done`. One imported
    `paid` (unstamped) + one genuine untouched approval: the rate is 100% off
    the ONE invoice that really cleared review, not 100% off two.
    """
    await _seed_statuses(
        realdb,
        [(InvoiceStatus.paid, False), (InvoiceStatus.approved, True)],
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert body["pipeline"]["paid"] == 1
    assert body["touchless_rate"] == 100.0

    # And it is genuinely absent from BOTH legs, not just the numerator: an
    # unstamped `paid` sitting beside a rejection cannot drag the rate down
    # either, because it never finished review.
    await _seed_statuses(realdb, [(InvoiceStatus.rejected, False)])
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()
    assert body["touchless_rate"] == 50.0


# ---------------------------------------------------------------------------
# 3b. touchless_rate — imported rows are outside BOTH legs
#
# The numerator fix above narrowed on EVIDENCE (`approval_date`). The
# denominator has the mirror hole and evidence cannot close it: nothing writes
# an approval stamp on a rejection, so gating the bounced leg would zero it
# outright. Provenance closes it instead — `services/csv_import` stamps
# `meta["imported"]` on every row it creates, and marked rows leave both legs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_touchless_rate_excludes_a_csv_imported_rejection(realdb):
    """A migrated historical `rejected` row is not a reviewer HERE bouncing an
    invoice back — the workflow engine never ran on it — so it must not
    deflate the automation figure.

    One genuine untouched approval + one CSV-imported rejection. Pre-fix the
    imported row padded the denominator: 1/2 -> 50.0%. It is now out of the
    population and the honest rate is 100.0%, off ONE invoice.
    """
    await _seed_statuses(realdb, [(InvoiceStatus.approved, True)])
    await _import_invoices(
        realdb,
        "invoice_number,vendor_name,amount,status\nHIST-R1,Legacy Supplies,500.00,rejected\n",
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    # The row really is there — this is an exclusion from the metric, not from
    # the tenant's data.
    assert body["pipeline"]["rejected"] == 1
    assert body["touchless_rate"] == 100.0


@pytest.mark.asyncio
async def test_touchless_rate_excludes_a_csv_imported_done_row_from_both_legs(realdb):
    """An imported `done` row was already out of the NUMERATOR (it carries no
    approval stamp). Confirm it is now out of the DENOMINATOR too, so it can
    never be promoted back into the population by an evidence count.

    Baseline: one stamped `done` + one native rejection = 50.0%. Importing
    three more historical `done` rows leaves it at 50.0% — and the stamped
    imported row in the second half proves provenance beats evidence: even
    carrying `approval_date`, it does not re-enter the numerator.
    """
    await _seed_statuses(
        realdb,
        [(InvoiceStatus.done, True), (InvoiceStatus.rejected, False)],
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        assert (await c.get("/api/dashboard")).json()["touchless_rate"] == 50.0

    await _import_invoices(
        realdb,
        "invoice_number,vendor_name,amount,status\n"
        "HIST-D1,Legacy Supplies,100.00,done\n"
        "HIST-D2,Legacy Supplies,200.00,done\n"
        "HIST-D3,Legacy Supplies,300.00,paid\n",
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()
    assert body["pipeline"]["done"] == 3
    assert body["touchless_rate"] == 50.0

    # An imported row that DOES carry an approval stamp (a future importer, or
    # a hand-repaired row) still stays out: provenance, not evidence, is what
    # removes it.
    await _seed_statuses(realdb, [(InvoiceStatus.done, True, _import_marker())])
    async with realdb.client(key=TENANT, role="admin") as c:
        assert (await c.get("/api/dashboard")).json()["touchless_rate"] == 50.0


@pytest.mark.asyncio
async def test_touchless_rate_still_counts_a_native_rejection(realdb):
    """Only PROVABLY imported rows leave the bounced leg. A tenant that never
    imports sees no change at all from this edit."""
    await _seed_statuses(
        realdb,
        [
            (InvoiceStatus.approved, True),
            (InvoiceStatus.rejected, False),
            (InvoiceStatus.rejected, False),
        ],
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert body["pipeline"]["rejected"] == 2
    assert body["touchless_rate"] == 33.3


@pytest.mark.asyncio
async def test_touchless_rate_treats_an_unmarked_row_as_native(realdb):
    """No marker means "we do not know", and the marker is written only going
    forward — so a row that predates it stays in the population rather than
    being reclassified on a guess.

    Both unmarked shapes are covered, because they behave differently in SQL:
    a row whose `meta` column was never written (SQL NULL — every invoice
    predating the marker) and one carrying OTHER keys (a cached audit summary,
    an `archived_at`). The rows are stamped `done`, which routes them through
    the evidence-count query where the distinction bites: Postgres' `?`
    operator returns NULL, not false, on a SQL-NULL jsonb, so a naive
    `NOT (meta ? 'imported')` drops every meta-less legacy row out of the
    NUMERATOR while leaving it in the denominator — 33.3% instead of 50.0%.
    """
    summary_meta = {"audit_summary": {"text": "auto-approved", "generated_at": "2026-01-01"}}
    await _seed_statuses(
        realdb,
        [
            (InvoiceStatus.done, True),  # meta IS NULL
            (InvoiceStatus.done, True, summary_meta),  # meta present, no marker
            (InvoiceStatus.rejected, False),  # meta IS NULL
            (InvoiceStatus.rejected, False, summary_meta),  # meta present, no marker
        ],
    )
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert body["touchless_rate"] == 50.0


# ---------------------------------------------------------------------------
# 4. monthly_trend — a calendar-month GROUP BY needs a calendar-month window
# ---------------------------------------------------------------------------


def _anchor(today: date) -> date:
    """First day of the month five calendar months back — the window the
    endpoint anchors to."""
    n = today.year * 12 + (today.month - 1) - 5
    return today.replace(year=n // 12, month=n % 12 + 1, day=1)


async def _seed_trend(realdb, dates: list[date]) -> None:
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        ent = await _default_entity_id(s)
        for d in dates:
            s.add(_inv(org_id, ent, status=InvoiceStatus.approved, invoice_date=d))
        await s.commit()


@pytest.mark.asyncio
async def test_monthly_trend_oldest_bucket_is_a_whole_month(realdb, monkeypatch):
    """`today = 2026-12-31`: the anchor is 2026-07-01, but `today - 180 days`
    is 2026-07-04 — three days INTO the oldest bucket's month.

    So the old window produced a "2026-07" bar built from 4–31 July only: a
    partial month drawn beside five whole ones, which reads as a spend
    collapse and shifts every single day as the window slides. An invoice
    dated 2026-07-02 is inside that month but outside the old window — pre-fix
    it is missing from the trend entirely.
    """
    frozen = date(2026, 12, 31)
    monkeypatch.setattr("app.api.dashboard.utc_today", lambda: frozen)
    anchor = _anchor(frozen)
    assert anchor == date(2026, 7, 1)
    assert anchor < frozen - timedelta(days=180)  # the partial-bucket shape

    await _seed_trend(realdb, [anchor + timedelta(days=1), date(2026, 12, 15)])
    async with realdb.client(key=TENANT, role="admin") as c:
        trend = (await c.get("/api/dashboard")).json()["monthly_trend"]

    months = [r["month"] for r in trend]
    assert months == sorted(months)
    assert len(months) == len(set(months)) <= 6
    assert months[0] == "2026-07"
    assert next(r for r in trend if r["month"] == "2026-07")["count"] == 1


@pytest.mark.asyncio
async def test_monthly_trend_has_no_seventh_stub_bucket(realdb, monkeypatch):
    """The mirror shape. `today = 2026-06-15`: the anchor is 2026-01-01 but
    `today - 180 days` is 2025-12-17, so the old window reached back into a
    SEVENTH month and drew a bar for 17–31 December — 15 days of data labelled
    as a month, next to six whole ones.

    Pre-fix `months[0] == "2025-12"` and there are seven buckets.
    """
    frozen = date(2026, 6, 15)
    monkeypatch.setattr("app.api.dashboard.utc_today", lambda: frozen)
    anchor = _anchor(frozen)
    assert anchor == date(2026, 1, 1)
    assert anchor > frozen - timedelta(days=180)  # the seventh-bucket shape

    await _seed_trend(realdb, [date(2025, 12, 20), anchor, date(2026, 6, 1)])
    async with realdb.client(key=TENANT, role="admin") as c:
        trend = (await c.get("/api/dashboard")).json()["monthly_trend"]

    months = [r["month"] for r in trend]
    assert "2025-12" not in months
    assert months[0] == "2026-01"
    assert len(months) <= 6
