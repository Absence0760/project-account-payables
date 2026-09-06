"""The dashboard's top-vendor tile, grouped in SQL — equivalence + ordering.

`GET /api/dashboard`'s "spend by vendor" tile used to `SELECT` five columns of
EVERY non-rejected invoice (all time, no `LIMIT`) and fold them in Python via
`currency_conversion.vendor_rollup_to_reporting_currency`, while the rollup,
pipeline, aging and trend blocks immediately around it all `GROUP BY` in SQL.
That fold was also a synchronous per-row loop inside an `async def` — the shape
the project invariant *"blocking work does not run on the event loop"* forbids —
and it grew linearly with the invoice table.

It is now a `GROUP BY … ORDER BY … LIMIT 10` over the same `_rep_expr` CASE the
sibling blocks use. These tests are the proof that the rewrite did not change
the answer:

* `test_sql_grouping_matches_python_fold_over_randomised_book` builds a book
  whose dimensions vary **independently** — vendor, amount, status, invoice
  date, currency and whether a reporting-currency lock exists are each drawn
  from their own generator — and asserts the endpoint returns exactly what the
  old Python fold produces over the same rows. Correlated generators are how an
  aggregation bug hides (`docs/decisions.md` §82): if every EUR invoice also
  happened to be the largest, or every rejected one belonged to one vendor, a
  wrong `GROUP BY` key or a mis-scoped `WHERE` would still look right.
* the tie tests pin the ORDERING RULE, which the rewrite deliberately made
  stricter — see below.
* the multi-currency tests pin the conversion rule, including the
  unconvertible-row fallback.

**Ties.** The Python fold sorted on the converted total alone, and Python's sort
is stable, so equal-spend vendors came out in DB scan order — which of two took
rank 10, and which fell off the `[:10]`, could differ between two identical
requests. The SQL orders `(total DESC, vendor ASC)`. So the equivalence
assertion is against the fold's output *re-sorted by that rule*: same numbers,
same membership, now reproducible. `test_ties_are_broken_by_vendor_name` and
`test_repeated_requests_are_stable_across_a_tie_at_the_cutoff` pin that
directly.

Vendor names here are deliberately uniform in shape (`EQV-01`, `TIE-A`) so that
Postgres' collation and Python's `str` ordering agree — the tests are about the
aggregation, not about locale-dependent collation of mixed-case/punctuated text.
"""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.services.currency_conversion import vendor_rollup_to_reporting_currency

TENANT = "a"

# Every status the endpoint's `status != "rejected"` filter must let through,
# plus `rejected` itself so the exclusion is exercised.
_STATUSES = [
    InvoiceStatus.new,
    InvoiceStatus.pending,
    InvoiceStatus.ready_for_review,
    InvoiceStatus.approved,
    InvoiceStatus.sent_to_erp,
    InvoiceStatus.posted_in_erp,
    InvoiceStatus.payment_scheduled,
    InvoiceStatus.paid,
    InvoiceStatus.done,
    InvoiceStatus.rejected,
]


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


# ---------------------------------------------------------------------------
# The reference implementation — the code the endpoint used to run, verbatim in
# behaviour. Kept here (not imported from the endpoint, which no longer has it)
# so the equivalence assertion has something independent to compare against.
# ---------------------------------------------------------------------------


def _python_fold_reference(rows, *, reporting_currency: str):
    """What the pre-SQL dashboard produced, given the rows it used to select.

    `rows` are `(vendor_name, amount, currency, reporting_amount,
    reporting_currency)` tuples for every invoice the old `WHERE` admitted.
    """
    return vendor_rollup_to_reporting_currency(
        [
            {
                "vendor": vendor,
                "amount": amount,
                "currency": currency,
                "reporting_amount": rep_amt,
                "reporting_currency": rep_cur,
            }
            for vendor, amount, currency, rep_amt, rep_cur in rows
        ],
        reporting_currency=reporting_currency,
    )


def _expected_top10(entries):
    """The fold's entries under the SQL's ordering rule: total DESC, name ASC.

    The fold itself sorted on the total alone, leaving ties to DB scan order.
    Applying the name tiebreak here is what makes the comparison meaningful for
    a tie — it isolates "did the numbers change" (must be no) from "did the tie
    order become deterministic" (must be yes).
    """
    ranked = sorted(entries, key=lambda e: (-e.amount, e.vendor))
    return [(e.vendor, e.amount) for e in ranked[:10]]


async def _fold_reference_from_db(realdb, *, reporting_currency="USD", entity_id=None):
    """Re-read the invoices the OLD query would have selected and fold them."""
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        q = select(
            Invoice.vendor_name,
            Invoice.amount,
            Invoice.currency,
            Invoice.reporting_amount,
            Invoice.reporting_currency,
        ).where(
            Invoice.vendor_name.isnot(None),
            Invoice.vendor_name != "",
            Invoice.status != "rejected",
        )
        if entity_id is not None:
            q = q.where(Invoice.entity_id == entity_id)
        rows = (await s.execute(q)).all()
    return _expected_top10(_python_fold_reference(rows, reporting_currency=reporting_currency))


def _actual_top10(body):
    return [(v["vendor"], Decimal(str(v["amount"]))) for v in body["vendor_spend"]]


# ---------------------------------------------------------------------------
# Equivalence over an independently-randomised book
# ---------------------------------------------------------------------------


async def _seed_randomised_book(realdb, *, seed: int, count: int = 140, vendors: int = 14):
    """Seed `count` invoices whose dimensions are drawn INDEPENDENTLY.

    Each of vendor, amount, status, invoice date, currency and the presence (and
    currency) of a rate lock comes from its own draw. Nothing is correlated, so
    a mis-keyed GROUP BY, a mis-scoped WHERE, or a conversion rule applied to
    the wrong subset shows up instead of cancelling out.
    """
    rng = random.Random(seed)
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = date.today()
    names = [f"EQV-{i:02d}" for i in range(vendors)]

    async with mk() as s:
        ent = await _default_entity_id(s)
        for i in range(count):
            vendor = rng.choice(names)
            # Cents drawn independently of the units, so no amount is a round
            # number by construction.
            amount = Decimal(rng.randrange(1_00, 90_000_00)) / Decimal(100)
            status = rng.choice(_STATUSES)
            invoice_date = today - timedelta(days=rng.randrange(0, 400))
            currency = rng.choice(["USD", "EUR", "GBP", "JPY"])

            # The lock is drawn independently of the currency, so all four
            # per-row cases the CASE expression must distinguish occur:
            #   locked at the target currency  -> use reporting_amount
            #   locked at a NON-target currency-> ignore, fall back to amount
            #   no lock, currency == target    -> 1:1
            #   no lock, currency != target    -> face value, "unconverted"
            lock = rng.choice(["target", "other", "none", "none"])
            if lock == "target":
                rep_cur = "USD"
                rep_amt = (amount * Decimal(rng.randrange(80, 130)) / Decimal(100)).quantize(
                    Decimal("0.01")
                )
            elif lock == "other":
                rep_cur = "CHF"
                rep_amt = amount * Decimal("2")
            else:
                rep_cur, rep_amt = None, None

            s.add(
                Invoice(
                    organization_id=org_id,
                    entity_id=ent,
                    invoice_number=f"EQV-{seed}-{i:04d}",
                    vendor_name=vendor,
                    amount=amount,
                    currency=currency,
                    reporting_currency=rep_cur,
                    reporting_amount=rep_amt,
                    status=status,
                    invoice_date=invoice_date,
                )
            )
        # Rows the tile must EXCLUDE, independent of everything above: a blank
        # vendor name on an otherwise-payable invoice.
        s.add(
            Invoice(
                organization_id=org_id,
                entity_id=ent,
                invoice_number=f"EQV-{seed}-BLANK",
                vendor_name="",
                amount=Decimal("999999.00"),
                currency="USD",
                status=InvoiceStatus.approved,
                invoice_date=today,
            )
        )
        await s.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [11, 4242, 90210])
async def test_sql_grouping_matches_python_fold_over_randomised_book(realdb, seed):
    """The SQL GROUP BY returns exactly what the Python fold returned.

    Three independent seeds, so the equality is not a property of one lucky
    draw. Each seed's book covers every status, four currencies, all four
    rate-lock cases and an excluded blank-vendor row.
    """
    await _seed_randomised_book(realdb, seed=seed)
    expected = await _fold_reference_from_db(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()

    assert len(expected) == 10, "fixture must overflow the LIMIT for the cutoff to be tested"
    assert _actual_top10(body) == expected
    assert all(v["vendor"] != "" for v in body["vendor_spend"])


# ---------------------------------------------------------------------------
# Ties — the ordering rule the rewrite made deterministic
# ---------------------------------------------------------------------------


async def _seed_tie_book(realdb):
    """Eight vendors above the tie, five tied EXACTLY at the rank-9 total.

    Only two of the five tied vendors fit inside the top 10, so the tie
    straddles the cutoff — which is the case that used to vary run to run.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = date.today()
    async with mk() as s:
        ent = await _default_entity_id(s)
        n = 0
        for rank in range(8):
            n += 1
            s.add(
                Invoice(
                    organization_id=org_id,
                    entity_id=ent,
                    invoice_number=f"TIEBOOK-{n:04d}",
                    vendor_name=f"TOP-{rank}",
                    amount=Decimal(10_000 - rank * 100),
                    currency="USD",
                    status=InvoiceStatus.approved,
                    invoice_date=today,
                )
            )
        # Deliberately inserted in REVERSE alphabetical order: insertion order is
        # the tiebreak the old fold fell back on, so if the endpoint were still
        # ordering by total alone this would surface as E,D,C rather than A,B,C.
        for letter in ["E", "D", "C", "B", "A"]:
            # Two invoices each, so the tie is on a SUM rather than on a single
            # row the planner might return in insertion order anyway.
            for part in (Decimal("300.00"), Decimal("200.00")):
                n += 1
                s.add(
                    Invoice(
                        organization_id=org_id,
                        entity_id=ent,
                        invoice_number=f"TIEBOOK-{n:04d}",
                        vendor_name=f"TIE-{letter}",
                        amount=part,
                        currency="USD",
                        status=InvoiceStatus.approved,
                        invoice_date=today,
                    )
                )
        await s.commit()


@pytest.mark.asyncio
async def test_ties_are_broken_by_vendor_name(realdb):
    await _seed_tie_book(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()
    rows = _actual_top10(body)

    assert [v for v, _ in rows][:8] == [f"TOP-{i}" for i in range(8)]
    # Two of the five tied vendors fit; they must be the alphabetically first
    # two, not the two the insert order happened to produce (E, D).
    assert [v for v, _ in rows][8:] == ["TIE-A", "TIE-B"]
    assert all(amt == Decimal("500.00") for v, amt in rows if v.startswith("TIE-"))

    # And the numbers still match the old fold exactly.
    assert rows == await _fold_reference_from_db(realdb)


@pytest.mark.asyncio
async def test_repeated_requests_are_stable_across_a_tie_at_the_cutoff(realdb):
    """The property the old fold could not offer: the same answer twice."""
    await _seed_tie_book(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        first = _actual_top10((await c.get("/api/dashboard")).json())
        second = _actual_top10((await c.get("/api/dashboard")).json())
        third = _actual_top10((await c.get("/api/dashboard")).json())
    assert first == second == third


# ---------------------------------------------------------------------------
# Multi-currency — conversion rule and the unconvertible-row fallback
# ---------------------------------------------------------------------------


async def _seed_currency_cases(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = date.today()

    def inv(s, ent, num, vendor, amount, currency, rep_cur=None, rep_amt=None):
        s.add(
            Invoice(
                organization_id=org_id,
                entity_id=ent,
                invoice_number=num,
                vendor_name=vendor,
                amount=amount,
                currency=currency,
                reporting_currency=rep_cur,
                reporting_amount=rep_amt,
                status=InvoiceStatus.approved,
                invoice_date=today,
            )
        )

    async with mk() as s:
        ent = await _default_entity_id(s)
        # Converted: a USD row plus a EUR row carrying a USD rate lock.
        inv(s, ent, "CCY-1", "CCY Locked Co", Decimal("1000.00"), "USD")
        inv(s, ent, "CCY-2", "CCY Locked Co", Decimal("1000.00"), "EUR", "USD", Decimal("1086.96"))
        # Unconvertible: a foreign row with NO usable lock. Both the old fold
        # and the SQL fall back to FACE value here.
        inv(s, ent, "CCY-3", "CCY Unlocked Co", Decimal("500.00"), "USD")
        inv(s, ent, "CCY-4", "CCY Unlocked Co", Decimal("400.00"), "GBP")
        # Lock present but denominated in a THIRD currency — must be ignored,
        # not treated as if it were the reporting figure.
        inv(s, ent, "CCY-5", "CCY Wrong Lock Co", Decimal("700.00"), "EUR", "CHF", Decimal("9999"))
        await s.commit()


@pytest.mark.asyncio
async def test_multi_currency_totals_match_the_python_fold(realdb):
    await _seed_currency_cases(realdb)
    expected = await _fold_reference_from_db(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()
    rows = _actual_top10(body)
    assert rows == expected

    by_vendor = dict(rows)
    # Rate-locked EUR row converts: 1000.00 + 1086.96, not the naive 2000.00.
    assert by_vendor["CCY Locked Co"] == Decimal("2086.96")
    # Unconvertible foreign row falls back to FACE value — 500 + 400 — exactly
    # as the Python fold did. The dashboard's `vendor_spend` schema is
    # `{vendor, amount}` with nowhere to report it, so this fallback is silent
    # here, the same way it is for `aging_reporting` and `monthly_trend`. Only
    # the whole-book `reporting` rollup carries an `unconverted_count`.
    assert by_vendor["CCY Unlocked Co"] == Decimal("900.00")
    # A lock in a third currency is ignored, not read as the reporting figure.
    assert by_vendor["CCY Wrong Lock Co"] == Decimal("700.00")


@pytest.mark.asyncio
async def test_unconverted_rows_are_counted_by_the_whole_book_rollup(realdb):
    """The unconverted rows the vendor tile folds at face value are not
    invisible platform-wide — the `reporting` rollup on the same response
    counts them. Pinning it here so the silent fallback above stays a *known*
    trade-off rather than a total blind spot."""
    await _seed_currency_cases(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()
    # CCY-4 (GBP, no lock) and CCY-5 (EUR, CHF lock) cannot be converted.
    assert body["reporting"]["unconverted_count"] == 2


# ---------------------------------------------------------------------------
# Scoping — tenant/entity narrowing must survive the rewrite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vendor_spend_is_scoped_to_the_selected_entity(realdb):
    """`X-Entity-ID` still narrows the tile, and the narrowed answer still
    equals the Python fold over that entity's rows."""
    from app.models.entity import Entity

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = date.today()
    sub_id = uuid.uuid4()
    async with mk() as s:
        default_id = await _default_entity_id(s)
        s.add(
            Entity(
                id=sub_id,
                organization_id=org_id,
                name="Subsidiary",
                slug=f"sub-{sub_id.hex[:8]}",
                is_default=False,
                is_active=True,
            )
        )
        for ent, vendor, amount, num in (
            (default_id, "ENT Default Co", Decimal("1000.00"), "ENT-1"),
            (sub_id, "ENT Sub Co", Decimal("2500.00"), "ENT-2"),
        ):
            s.add(
                Invoice(
                    organization_id=org_id,
                    entity_id=ent,
                    invoice_number=num,
                    vendor_name=vendor,
                    amount=amount,
                    currency="USD",
                    status=InvoiceStatus.approved,
                    invoice_date=today,
                )
            )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as c:
        consolidated = _actual_top10((await c.get("/api/dashboard")).json())
        scoped = _actual_top10(
            (await c.get("/api/dashboard", headers={"X-Entity-ID": str(sub_id)})).json()
        )

    assert dict(consolidated) == {
        "ENT Default Co": Decimal("1000.00"),
        "ENT Sub Co": Decimal("2500.00"),
    }
    assert scoped == [("ENT Sub Co", Decimal("2500.00"))]
    assert scoped == await _fold_reference_from_db(realdb, entity_id=sub_id)


@pytest.mark.asyncio
async def test_rejected_invoices_stay_excluded_after_the_rewrite(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = date.today()
    async with mk() as s:
        ent = await _default_entity_id(s)
        for num, amount, status in (
            ("REJ-OK", Decimal("1000.00"), InvoiceStatus.approved),
            ("REJ-NO", Decimal("9000.00"), InvoiceStatus.rejected),
        ):
            s.add(
                Invoice(
                    organization_id=org_id,
                    entity_id=ent,
                    invoice_number=num,
                    vendor_name="REJ Vendor Co",
                    amount=amount,
                    currency="USD",
                    status=status,
                    invoice_date=today,
                )
            )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()
    assert dict(_actual_top10(body)) == {"REJ Vendor Co": Decimal("1000.00")}


@pytest.mark.asyncio
async def test_empty_tenant_returns_no_vendor_rows(realdb):
    """`coalesce(sum(...), 0)` plus GROUP BY: no rows, not a single `(None, 0)`."""
    async with realdb.client(key=TENANT, role="admin") as c:
        body = (await c.get("/api/dashboard")).json()
    assert body["vendor_spend"] == []
