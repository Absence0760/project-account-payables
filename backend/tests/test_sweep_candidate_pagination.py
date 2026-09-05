"""Keyset pagination for the last two sweeps that read their whole candidate set.

``docs/background-sweeps.md`` § Locking states the rule these two were the
outstanding exception to: **page, don't cap, unless the work removes itself from
the candidate set.** Neither qualifies —

* ``discount_auto_trigger``: an offer skipped for a below-threshold ROI stays
  ``offered``;
* ``contract_renewal``: a contract outside its lead window stays un-alerted, and
  one that is not yet over term stays ``active``.

so a per-tick ``LIMIT`` would re-serve the same lowest-id rows on every tick and
never reach the tail (the starvation ``approval_escalation`` was rewritten to
avoid). Both now keyset-paginate (``WHERE id > :cursor ORDER BY id LIMIT n``)
until the tenant is exhausted, with ``FEOH_DISCOUNT_OPTIMIZATION_BATCH_SIZE`` /
``FEOH_CONTRACT_RENEWAL_BATCH_SIZE`` as the page size.

The second half of this file guards ``contract_renewal``'s alert pre-filter,
which moved out of Python and into SQL: ``end_date - today <=
COALESCE(renewal_notice_days, <platform default>)``. A per-row interval
expression is exactly the kind of thing that type-coerces differently in SQL than
in Python, so the SQL predicate and the under-lock Python re-check are asserted
to agree on every boundary — one day before the window, exactly on it, one day
after — including the ``NULL`` notice-days fallback the NOT NULL column itself
cannot hold.

Every test runs the REAL sweep against ``realdb`` with a deliberately tiny page
size, so a regression to one unbounded ``SELECT`` (or to a cap) fails here rather
than becoming a memory ceiling nobody sees until a large tenant hits it.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import Date, Integer, cast, func, literal, select

from app.config import settings as cfg
from app.models.contract import Contract, ContractStatus, ContractType
from app.models.discount import (
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_OFFERED,
    DiscountOffer,
)
from app.models.payment import Payment, PaymentRun
from app.models.vendor import Vendor
from app.services import contract_renewal, discount_auto_trigger
from app.services.contract_renewal import (
    lead_window_predicate,
    resolve_notice_days,
    within_lead_window,
)

_TODAY = date(2026, 1, 1)


def _oid(n: int) -> uuid.UUID:
    """Deterministic, strictly increasing ids — both sweeps order by id, so the
    page a row lands on has to be predictable for these assertions to mean
    anything."""
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


@contextlib.contextmanager
def _page_size(attr: str, size: int):
    original = getattr(cfg, attr)
    setattr(cfg, attr, size)
    try:
        yield
    finally:
        setattr(cfg, attr, original)


class _Poison(RuntimeError):
    """Deterministic per-row failure — the shape a malformed row produces."""


# ---------------------------------------------------------------------------
# discount_auto_trigger
# ---------------------------------------------------------------------------


def _offer(org_id: uuid.UUID, offer_id: uuid.UUID, *, percent: str) -> DiscountOffer:
    """One `offered` offer. ``percent="3.00"`` over ~20 days accelerated is
    ~45% APR — comfortably over the 12% auto-capture threshold; ``"0.01"`` is
    far under it, so the sweep SKIPS the offer and it stays a candidate."""
    return DiscountOffer(
        id=offer_id,
        organization_id=org_id,
        scope="invoice",
        source="supplier",
        status=OFFER_STATUS_OFFERED,
        tiers=[{"days": 5, "percent": percent}],
        base_amount=Decimal("10000.00"),
        currency="USD",
        valid_from=_TODAY,
        valid_until=date(2026, 1, 26),
    )


async def _resolver_const(_org_id):
    return Decimal("8.00")


async def _seed_offers(mk, org_id, specs: list[tuple[int, str]]) -> None:
    async with mk() as db:
        for n, percent in specs:
            db.add(_offer(org_id, _oid(n), percent=percent))
        await db.commit()


def _record_visited_offers(seen: list[uuid.UUID], poison: uuid.UUID | None = None):
    """Patch the per-offer due-date lookup to record which offers the sweep
    actually reached (and optionally poison one of them)."""
    real = discount_auto_trigger._resolve_due_date

    async def _fake(db, offer):
        seen.append(offer.id)
        if poison is not None and offer.id == poison:
            raise _Poison("malformed row")
        return await real(db, offer)

    return patch.object(discount_auto_trigger, "_resolve_due_date", _fake)


async def _offer_statuses(mk) -> dict[uuid.UUID, str]:
    async with mk() as db:
        rows = (await db.execute(select(DiscountOffer.id, DiscountOffer.status))).all()
    return dict(rows)


@pytest.mark.asyncio
async def test_discount_sweep_visits_every_candidate_across_pages_exactly_once(realdb):
    """Five candidates, pages of two: all five are reached, none twice.

    Re-serving is the failure a naive ``LIMIT``/``OFFSET`` produces once rows
    leave the candidate set mid-tick (each acceptance shifts the offsets); never
    reaching the tail is the failure a bare ``LIMIT`` produces. The keyset cursor
    rules out both, and asserting the visit LIST — not just the outcome count —
    is what distinguishes them.
    """
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    await _seed_offers(mk, info.org_id, [(n, "3.00") for n in range(1, 6)])

    seen: list[uuid.UUID] = []
    with _page_size("discount_optimization_batch_size", 2), _record_visited_offers(seen):
        outcome = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    assert outcome.captured == 5
    assert outcome.offer_failures == 0
    assert seen == [_oid(n) for n in range(1, 6)], "each candidate once, in id order"
    assert all(s == OFFER_STATUS_ACCEPTED for s in (await _offer_statuses(mk)).values())


@pytest.mark.asyncio
async def test_discount_sweep_pages_past_offers_it_declines_to_capture(realdb):
    """A skipped offer never leaves the candidate set — the exact reason this
    sweep may not cap. With pages of two and the first FOUR offers below the ROI
    threshold, a cap of two would stop at the two skipped rows on page one and
    never reach the worthwhile fifth, forever."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    await _seed_offers(
        mk,
        info.org_id,
        [(1, "0.01"), (2, "0.01"), (3, "0.01"), (4, "0.01"), (5, "3.00")],
    )

    seen: list[uuid.UUID] = []
    with _page_size("discount_optimization_batch_size", 2), _record_visited_offers(seen):
        outcome = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    assert seen == [_oid(n) for n in range(1, 6)]
    assert outcome.captured == 1
    statuses = await _offer_statuses(mk)
    assert statuses[_oid(5)] == OFFER_STATUS_ACCEPTED
    assert [statuses[_oid(n)] for n in range(1, 5)] == [OFFER_STATUS_OFFERED] * 4


@pytest.mark.asyncio
async def test_a_failed_offer_on_page_one_does_not_starve_the_later_pages(realdb):
    """Per-item isolation is what makes the pagination mean anything: the cursor
    is a local that resets every tick, so a raise escaping the page loop would
    abort at the same row on every tick and nothing after it would ever be
    captured."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    await _seed_offers(mk, info.org_id, [(n, "3.00") for n in range(1, 6)])

    seen: list[uuid.UUID] = []
    with (
        _page_size("discount_optimization_batch_size", 2),
        _record_visited_offers(seen, poison=_oid(1)),
    ):
        outcome = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    assert seen == [_oid(n) for n in range(1, 6)]
    assert outcome.captured == 4
    assert outcome.offer_failures == 1
    statuses = await _offer_statuses(mk)
    assert statuses[_oid(1)] == OFFER_STATUS_OFFERED
    assert [statuses[_oid(n)] for n in range(2, 6)] == [OFFER_STATUS_ACCEPTED] * 4


# ---------------------------------------------------------------------------
# contract_renewal — pagination
# ---------------------------------------------------------------------------


def _contract(
    org_id: uuid.UUID,
    vendor_id: uuid.UUID,
    contract_id: uuid.UUID,
    *,
    number: str,
    end_date: date,
    owner_user_id: uuid.UUID,
    notice_days: int = 30,
    alert_sent: datetime | None = None,
) -> Contract:
    return Contract(
        id=contract_id,
        contract_number=number,
        title=number,
        contract_type=ContractType.msa,
        status=ContractStatus.active,
        vendor_id=vendor_id,
        currency="USD",
        start_date=_TODAY - timedelta(days=365),
        end_date=end_date,
        renewal_notice_days=notice_days,
        renewal_alert_sent_at=alert_sent,
        owner_user_id=owner_user_id,
        organization_id=org_id,
    )


async def _seed_contracts(mk, org_id, *contracts_kwargs) -> None:
    async with mk() as db:
        vendor = Vendor(id=uuid.uuid4(), organization_id=org_id, name="Globex Industrial")
        db.add(vendor)
        await db.flush()
        for kwargs in contracts_kwargs:
            db.add(_contract(org_id, vendor.id, **kwargs))
        await db.commit()


def _record_alerted(seen: list[str], poison: str | None = None):
    """Record which contracts the ALERT pass rendered a notification for."""
    real = contract_renewal.render_contract_renewal

    def _fake(*, contract_number, **kwargs):
        seen.append(contract_number)
        if poison is not None and contract_number == poison:
            raise _Poison("malformed row")
        return real(contract_number=contract_number, **kwargs)

    return patch.object(contract_renewal, "render_contract_renewal", _fake)


def _record_expired(seen: list[str]):
    """Record which contracts the EXPIRY pass wrote a `contract.expired` row
    for — that audit write is the pass's only per-row side effect."""
    real = contract_renewal.dispatch_audit

    async def _fake(db, **kwargs):
        number = (kwargs.get("details") or {}).get("contract_number")
        if number is not None:
            seen.append(number)
        return await real(db, **kwargs)

    return patch.object(contract_renewal, "dispatch_audit", _fake)


async def _contracts_by_number(mk) -> dict[str, Contract]:
    async with mk() as db:
        rows = (await db.execute(select(Contract))).scalars().all()
    return {c.contract_number: c for c in rows}


@pytest.mark.asyncio
async def test_alert_pass_visits_every_candidate_across_pages_exactly_once(realdb):
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    owner = info.users["ap_manager"]
    await _seed_contracts(
        mk,
        info.org_id,
        *[
            {
                "contract_id": _oid(n),
                "number": f"PAGE-{n:02d}",
                "end_date": _TODAY + timedelta(days=10),
                "owner_user_id": owner,
            }
            for n in range(1, 6)
        ],
    )

    seen: list[str] = []
    with _page_size("contract_renewal_batch_size", 2), _record_alerted(seen):
        outcome = await contract_renewal._sweep_tenant(info.db_name, _TODAY)

    assert outcome.alerts_sent == 5
    assert seen == [f"PAGE-{n:02d}" for n in range(1, 6)]
    rows = await _contracts_by_number(mk)
    assert all(rows[f"PAGE-{n:02d}"].renewal_alert_sent_at is not None for n in range(1, 6))


@pytest.mark.asyncio
async def test_expiry_pass_pages_through_every_overdue_contract(realdb):
    """The second pass carries its own cursor. Sharing one with the alert pass
    would make the second pass start wherever the first happened to stop."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    owner = info.users["ap_manager"]
    already = datetime.now(UTC)
    await _seed_contracts(
        mk,
        info.org_id,
        *[
            {
                "contract_id": _oid(n),
                "number": f"OVER-{n:02d}",
                "end_date": _TODAY - timedelta(days=5),
                "owner_user_id": owner,
                "alert_sent": already,  # alert pass has nothing to do here
            }
            for n in range(1, 6)
        ],
    )

    seen: list[str] = []
    with _page_size("contract_renewal_batch_size", 2), _record_expired(seen):
        outcome = await contract_renewal._sweep_tenant(info.db_name, _TODAY)

    assert outcome.alerts_sent == 0
    assert outcome.contracts_expired == 5
    assert seen == [f"OVER-{n:02d}" for n in range(1, 6)]
    rows = await _contracts_by_number(mk)
    assert all(rows[f"OVER-{n:02d}"].status == ContractStatus.expired for n in range(1, 6))


@pytest.mark.asyncio
async def test_a_failed_alert_on_page_one_does_not_starve_the_later_pages(realdb):
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    owner = info.users["ap_manager"]
    await _seed_contracts(
        mk,
        info.org_id,
        *[
            {
                "contract_id": _oid(n),
                "number": f"MIX-{n:02d}",
                "end_date": _TODAY + timedelta(days=10),
                "owner_user_id": owner,
            }
            for n in range(1, 6)
        ],
    )

    seen: list[str] = []
    with _page_size("contract_renewal_batch_size", 2), _record_alerted(seen, poison="MIX-01"):
        outcome = await contract_renewal._sweep_tenant(info.db_name, _TODAY)

    assert seen == [f"MIX-{n:02d}" for n in range(1, 6)]
    assert outcome.alerts_sent == 4
    assert outcome.contract_failures == 1
    rows = await _contracts_by_number(mk)
    assert rows["MIX-01"].renewal_alert_sent_at is None
    assert all(rows[f"MIX-{n:02d}"].renewal_alert_sent_at is not None for n in range(2, 6))


# ---------------------------------------------------------------------------
# contract_renewal — the SQL lead window agrees with the Python re-check
# ---------------------------------------------------------------------------

# (days_until_end_date, renewal_notice_days) — one day inside the window,
# exactly on it, and one day outside, at three different per-row windows plus
# the zero-day edge.
_BOUNDARY_CASES = [
    (29, 30),
    (30, 30),
    (31, 30),
    (6, 7),
    (7, 7),
    (8, 7),
    (0, 0),
    (1, 0),
]


@pytest.mark.asyncio
async def test_sql_lead_window_agrees_with_the_python_recheck_on_every_boundary(realdb):
    """The SQL candidate query and the under-lock Python re-check are two
    expressions of one rule, in two languages, over a per-row interval. If they
    disagree by a day the sweep either alerts on a contract it then refuses to
    stamp (a candidate re-served every tick forever) or never alerts at all.

    Asserted end-to-end through the real sweep: a contract is alerted iff
    ``within_lead_window`` says it is.
    """
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    owner = info.users["ap_manager"]
    await _seed_contracts(
        mk,
        info.org_id,
        *[
            {
                "contract_id": _oid(i + 1),
                "number": f"BOUND-{days}-{notice}",
                "end_date": _TODAY + timedelta(days=days),
                "notice_days": notice,
                "owner_user_id": owner,
            }
            for i, (days, notice) in enumerate(_BOUNDARY_CASES)
        ],
    )

    outcome = await contract_renewal._sweep_tenant(info.db_name, _TODAY)

    rows = await _contracts_by_number(mk)
    expected = {
        f"BOUND-{days}-{notice}": within_lead_window(
            end_date=_TODAY + timedelta(days=days),
            renewal_notice_days=notice,
            ref_today=_TODAY,
        )
        for days, notice in _BOUNDARY_CASES
    }
    actual = {number: rows[number].renewal_alert_sent_at is not None for number in expected}
    assert actual == expected
    assert outcome.alerts_sent == sum(expected.values())
    # Sanity on the fixture itself: it must contain both verdicts, or the
    # assertion above is satisfied by a predicate that always answers the same.
    assert set(expected.values()) == {True, False}


@pytest.mark.asyncio
async def test_the_sql_predicate_itself_matches_python_including_a_null_notice(realdb):
    """``renewal_notice_days`` is ``NOT NULL DEFAULT 30``, so the NULL fallback
    cannot be reached through a seeded row — which is exactly why it is worth
    pinning. ``lead_window_predicate`` takes its columns as parameters, so the
    real expression is evaluated by real Postgres against synthetic values,
    NULL included, rather than a test re-implementing it.
    """
    mk = realdb.sessionmaker("a")
    default = int(cfg.contract_renewal_default_notice_days)
    cases = _BOUNDARY_CASES + [
        (default - 1, None),
        (default, None),
        (default + 1, None),
    ]

    async with mk() as db:
        for days, notice in cases:
            end_date = _TODAY + timedelta(days=days)
            sql_verdict = (
                await db.execute(
                    select(
                        lead_window_predicate(
                            _TODAY,
                            cast(literal(end_date), Date),
                            cast(literal(notice), Integer),
                        )
                    )
                )
            ).scalar_one()
            python_verdict = within_lead_window(
                end_date=end_date, renewal_notice_days=notice, ref_today=_TODAY
            )
            assert sql_verdict is python_verdict, (days, notice)

    assert resolve_notice_days(None) == default
    assert resolve_notice_days(7) == 7


# ---------------------------------------------------------------------------
# Neither sweep moves money
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_neither_sweep_creates_a_payment_or_payment_run(realdb):
    """Both sweeps are status/notification-only by design: capturing a discount
    flags ``offered -> accepted`` and the CFO-gated payment run still funds it,
    and a renewal alert moves nothing at all. Paginating them must not have
    quietly changed that."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    owner = info.users["ap_manager"]
    await _seed_offers(mk, info.org_id, [(n, "3.00") for n in range(1, 4)])
    await _seed_contracts(
        mk,
        info.org_id,
        {
            "contract_id": _oid(10),
            "number": "MONEY-01",
            "end_date": _TODAY + timedelta(days=10),
            "owner_user_id": owner,
        },
        {
            "contract_id": _oid(11),
            "number": "MONEY-02",
            "end_date": _TODAY - timedelta(days=5),
            "owner_user_id": owner,
            "alert_sent": datetime.now(UTC),
        },
    )

    with _page_size("discount_optimization_batch_size", 2):
        discount_outcome = await discount_auto_trigger._sweep_tenant(
            info.db_name, _TODAY, _resolver_const
        )
    with _page_size("contract_renewal_batch_size", 1):
        renewal_outcome = await contract_renewal._sweep_tenant(info.db_name, _TODAY)

    # The sweeps did real work — otherwise "no payments" proves nothing.
    assert discount_outcome.captured == 3
    assert renewal_outcome.alerts_sent == 1
    assert renewal_outcome.contracts_expired == 1

    async with mk() as db:
        assert (await db.execute(select(func.count()).select_from(Payment))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(PaymentRun))).scalar_one() == 0
