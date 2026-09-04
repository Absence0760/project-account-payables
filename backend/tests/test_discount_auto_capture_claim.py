"""The auto-capture sweep's row claim — a human decision always wins.

``discount_auto_trigger`` scans candidates with a PLAIN, unlocked
``SELECT ... WHERE status = 'offered'``, then does per-row async work
(cost-of-capital resolution, due-date lookup, ROI) before deciding. A
supplier or an AP user can decline or accept an offer inside that window.
The sweep used to mutate the stale ORM object and issue an unconditional
``UPDATE ... SET status`` at its single end-of-loop commit, so a *committed*
human decision was silently overwritten: the offer came back ``accepted``
with an append-only ``discount_offer.auto_accepted`` audit row asserting the
sweep had found it open. Because the trail is append-only, that false entry
could never be corrected.

The fix is ``_claim_if_still_offered``: immediately before every status write
(accept AND expiry) the row is re-read ``FOR UPDATE`` with a
``status = 'offered'`` predicate and ``populate_existing=True``; a row someone
else moved is skipped and nothing is audited.

``tests/test_discount_auto_trigger.py`` proves the happy path and the basic
mid-sweep decline. This file goes after the mechanism itself:

  * every non-``offered`` status, both as a starting state and as a state
    committed inside the race window (parameterized);
  * the LOCK, not merely the re-read — a human holding an uncommitted
    ``FOR UPDATE`` on the row makes the sweep *block*, proved by asking
    Postgres' own ``pg_stat_activity`` which backend is waiting and on what
    statement, and the sweep then loses once the decline commits;
  * the same exclusion in the other direction — a human arriving while the
    sweep holds the claim blocks and reads the sweep's committed result, never
    a stale ``offered``;
  * ``populate_existing`` — the claim returns the row's freshly committed
    values, not the stale identity-mapped copy;
  * idempotency, including after a lost race;
  * the ROI threshold boundary (at / just under / just over) and the
    ``worthwhile`` half of the same gate;
  * the money-path boundary the module docstring promises: the sweep flips a
    status and nothing else — no ``Payment``, no ``PaymentRun``, no
    ``PaymentSchedule``, no invoice transition, no ``captured_amount``.

Requires the dev Postgres (``pnpm db:up``); skips otherwise, like every other
``realdb`` test.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text, update

from app.config import settings
from app.models.discount import (
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_CAPTURED,
    OFFER_STATUS_DECLINED,
    OFFER_STATUS_EXPIRED,
    OFFER_STATUS_OFFERED,
    DiscountOffer,
)
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun, PaymentSchedule
from app.models.workflow import AuditLog
from app.services import discount_auto_trigger
from app.services.discount_roi import annualized_return

# `asyncio_mode = "auto"` (pyproject) runs every `async def test_*` without a
# marker, so the one synchronous test below needs no exemption.

# The sweep is always driven with an explicit reference date, so nothing here
# depends on the wall clock (or on the host's timezone).
_TODAY = date(2026, 1, 1)

_AUDIT_ACTION = "discount_offer.auto_accepted"

# The canonical worthwhile fixture used throughout: 3% for paying 20 days early
# (a `valid_from`-anchored 5-day tier against a `valid_until` 25 days out).
# Pinned against the pure primitive so the fixture's economics are stated, not
# assumed — if the ROI maths ever changes, the boundary tests below say why.
_TIER_PCT = Decimal("3.00")
_DAYS_ACCELERATED = 20
_APR = Decimal("56.44")


def test_the_reference_fixtures_apr_is_what_the_boundary_tests_assume():
    """Pins `_APR` to the shared ROI primitive rather than to a comment."""
    assert annualized_return(_TIER_PCT, _DAYS_ACCELERATED) == _APR


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _offer(
    org_id,
    *,
    status: str = OFFER_STATUS_OFFERED,
    percent: str = "3.00",
    tier_days: int = 5,
    base: str = "10000.00",
    valid_until: date | None = None,
    invoice_id=None,
) -> DiscountOffer:
    """One offer anchored on `_TODAY`.

    `valid_from` is stated (never left NULL) because a tier's window is
    measured from `offer_reference_date` — `valid_from`, else the row's
    DATABASE-clock `created_at`, which has nothing to do with `_TODAY`.
    Default `valid_until` is `_TODAY + 25`, so the 5-day tier's deadline is
    `_TODAY + 5` and the horizon is 20 days of acceleration.
    """
    return DiscountOffer(
        id=uuid.uuid4(),
        organization_id=org_id,
        scope="invoice",
        invoice_id=invoice_id,
        vendor_id=None,
        source="supplier",
        status=status,
        tiers=[{"days": tier_days, "percent": percent}],
        base_amount=Decimal(base),
        currency="USD",
        valid_from=_TODAY,
        valid_until=_TODAY + timedelta(days=25) if valid_until is None else valid_until,
    )


async def _seed(mk, offer: DiscountOffer):
    async with mk() as db:
        db.add(offer)
        await db.commit()
    return offer.id


async def _resolver_const(_org_id, value=Decimal("8.00")):
    """Cost-of-capital resolver stub — the platform default, no control plane."""
    return value


def _resolver_of(pct: str):
    async def _resolve(_org_id):
        return Decimal(pct)

    return _resolve


async def _read(mk, offer_id) -> DiscountOffer:
    async with mk() as db:
        return (
            await db.execute(select(DiscountOffer).where(DiscountOffer.id == offer_id))
        ).scalar_one()


async def _audit_rows(mk, offer_id) -> list[AuditLog]:
    async with mk() as db:
        return list(
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.action == _AUDIT_ACTION,
                        AuditLog.entity_id == offer_id,
                    )
                )
            )
            .scalars()
            .all()
        )


# Row-level lock contention: Postgres first takes a `tuple` lock, then waits on
# the holder's `transactionid`. Either is proof that the waiter asked for a
# LOCK on the row — a plain (non-locking) re-read produces no ungranted lock at
# all, so an empty result here means the claim is not locking.
_ROW_LOCK_TYPES = {"transactionid", "tuple"}

_BLOCKED_SQL = text(
    "SELECT a.query, l.locktype FROM pg_locks l "
    "JOIN pg_stat_activity a ON a.pid = l.pid "
    "WHERE NOT l.granted AND l.pid <> pg_backend_pid() "
    "AND a.datname = current_database() LIMIT 1"
)


async def _await_row_lock_wait(mk, *, timeout: float = 15.0) -> tuple[str, str] | None:
    """Wait until Postgres reports another backend in this database blocked on
    an UNGRANTED lock; return ``(statement, locktype)`` (``None`` on timeout).

    Asked of the server's own lock table from an independent session — never
    slept for — so "the sweep is now queued behind the human's row lock" is
    observed rather than hoped for. The locktype is what makes this evidence:
    a claim degraded back to an unlocked re-read would take no lock, wait for
    nothing, and never appear here.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        async with mk() as s:
            row = (await s.execute(_BLOCKED_SQL)).first()
        if row is not None:
            return (row[0] or "", row[1] or "")
        await asyncio.sleep(0.05)
    return None


@contextlib.asynccontextmanager
async def _running_sweep(coro, *, release: asyncio.Event | None = None):
    """Run a sweep as a task whose lifetime ends with the ``with`` block.

    Every test below parks the sweep mid-transaction, while it holds a row
    lock, in order to observe the block from a third session. A failed
    assertion inside such a block must not leave that task parked: the
    harness's per-test reset TRUNCATEs `discount_offers`, which needs an ACCESS
    EXCLUSIVE lock and would queue behind the orphaned transaction forever — so
    one failed assertion would surface as a HUNG suite rather than a failed
    test, in whatever unrelated file happened to run next.

    Releases the gate (so a parked sweep can finish), then awaits the task,
    cancelling it if it will not. Bounded, and swallows the task's own
    exception so the ORIGINAL assertion failure is what the test reports.
    """
    task = asyncio.create_task(coro)
    try:
        yield task
    finally:
        if release is not None:
            release.set()
        try:
            await asyncio.wait_for(task, timeout=30)
        except BaseException:  # noqa: BLE001 - cleanup must not mask the real failure
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


# ---------------------------------------------------------------------------
# A row already out of `offered` is never a candidate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seeded_status",
    [
        OFFER_STATUS_ACCEPTED,
        OFFER_STATUS_DECLINED,
        OFFER_STATUS_EXPIRED,
        OFFER_STATUS_CAPTURED,
    ],
)
async def test_sweep_never_touches_an_offer_that_is_not_offered(realdb, seeded_status):
    """`offered` is the only state the sweep may write from — for a
    high-ROI offer in every other state it must report nothing, change
    nothing, and audit nothing."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id, status=seeded_status))

    captured = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    assert captured == 0
    row = await _read(mk, offer_id)
    assert row.status == seeded_status
    assert row.accepted_at is None
    assert row.accepted_tier is None
    assert await _audit_rows(mk, offer_id) == []


# ---------------------------------------------------------------------------
# A status committed INSIDE the race window wins — every status
# ---------------------------------------------------------------------------


def _commit_status_midway(mk, offer_id, new_status: str):
    """Patch target for `_resolve_due_date` that commits `new_status` from a
    SEPARATE session before returning — landing it exactly in the window
    between the sweep's unlocked candidate read and its status write."""
    real = discount_auto_trigger._resolve_due_date
    fired = False

    async def _side_effect(db, offer):
        nonlocal fired
        if not fired:
            fired = True
            async with mk() as other:
                await other.execute(
                    update(DiscountOffer)
                    .where(DiscountOffer.id == offer_id)
                    .values(status=new_status)
                )
                await other.commit()
        return await real(db, offer)

    return _side_effect


@pytest.mark.parametrize(
    "raced_status",
    [
        OFFER_STATUS_DECLINED,
        OFFER_STATUS_ACCEPTED,
        OFFER_STATUS_EXPIRED,
        OFFER_STATUS_CAPTURED,
    ],
)
async def test_a_status_committed_mid_sweep_is_never_overwritten(realdb, raced_status):
    """The sweep read this row as `offered`; by the time it decides, someone
    else has committed `raced_status`. Pre-fix the sweep's unconditional
    UPDATE overwrote it with `accepted` and audited an acceptance that never
    happened."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    with patch.object(
        discount_auto_trigger,
        "_resolve_due_date",
        _commit_status_midway(mk, offer_id, raced_status),
    ):
        captured = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    assert captured == 0
    row = await _read(mk, offer_id)
    assert row.status == raced_status
    assert row.accepted_at is None
    assert row.accepted_tier is None
    # The append-only trail must not assert an acceptance that did not happen.
    assert await _audit_rows(mk, offer_id) == []


async def test_a_lost_race_leaves_nothing_for_a_later_sweep_to_pick_up(realdb):
    """Idempotency after losing: the declined row is not a candidate on any
    later tick, so no eventual sweep quietly finishes the clobber."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    with patch.object(
        discount_auto_trigger,
        "_resolve_due_date",
        _commit_status_midway(mk, offer_id, OFFER_STATUS_DECLINED),
    ):
        assert await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const) == 0

    for _ in range(2):
        assert await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const) == 0

    assert (await _read(mk, offer_id)).status == OFFER_STATUS_DECLINED
    assert await _audit_rows(mk, offer_id) == []


async def test_an_accepted_offer_is_audited_exactly_once_across_repeated_sweeps(realdb):
    """The other half of idempotency: winning once must not audit twice."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    first = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)
    second = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)
    third = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    assert (first, second, third) == (1, 0, 0)
    assert len(await _audit_rows(mk, offer_id)) == 1
    assert (await _read(mk, offer_id)).status == OFFER_STATUS_ACCEPTED


# ---------------------------------------------------------------------------
# The LOCK itself — proved against Postgres' own wait state
# ---------------------------------------------------------------------------


async def test_sweep_blocks_on_a_humans_uncommitted_row_lock_and_then_loses(realdb):
    """The hard proof that the claim is a real row lock, not just a re-read.

    A human session takes the row (``SELECT ... FOR UPDATE`` + an uncommitted
    ``UPDATE ... SET status = 'declined'``). The sweep's unlocked candidate
    scan still sees `offered` (READ COMMITTED), so it proceeds — and then
    *blocks* at its claim. That block is asserted from an independent session
    via `pg_stat_activity`, including the statement it is stuck on. Once the
    human commits, the claim re-evaluates its `status = 'offered'` predicate
    against the newly committed row, finds nothing, and skips.
    """
    mk = realdb.sessionmaker("a")
    watcher_mk = realdb.sessionmaker("a")
    human_mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    async with human_mk() as human:
        # Hold the row: lock it, then write the decline WITHOUT committing.
        locked = (
            await human.execute(
                select(DiscountOffer).where(DiscountOffer.id == offer_id).with_for_update()
            )
        ).scalar_one()
        assert locked.status == OFFER_STATUS_OFFERED
        await human.execute(
            update(DiscountOffer)
            .where(DiscountOffer.id == offer_id)
            .values(status=OFFER_STATUS_DECLINED)
        )

        async with _running_sweep(
            discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)
        ) as sweep:
            waiting = await _await_row_lock_wait(watcher_mk)
            assert waiting is not None, "the sweep never blocked — the claim took no row lock"
            blocked_sql, locktype = waiting
            assert "discount_offers" in blocked_sql.lower()
            assert locktype in _ROW_LOCK_TYPES, (
                "the sweep is not waiting on a ROW lock; the claim must take FOR "
                f"UPDATE at the point of mutation. Waiting on {locktype}: {blocked_sql}"
            )
            assert not sweep.done()

            # Release: the human's decision lands.
            await human.commit()
            captured = await sweep

    assert captured == 0
    row = await _read(mk, offer_id)
    assert row.status == OFFER_STATUS_DECLINED
    assert row.accepted_at is None
    assert await _audit_rows(mk, offer_id) == []


async def test_a_human_arriving_while_the_sweep_holds_the_claim_blocks_and_sees_the_result(
    realdb,
):
    """The exclusion runs both ways.

    Held at its audit write (so its transaction still owns the claim), the
    sweep must block a human's own locking read of the same row. The human
    therefore decides against the sweep's committed `accepted`, never against
    a stale `offered` — which is what stops the two from trading clobbers in
    the opposite order.
    """
    mk = realdb.sessionmaker("a")
    watcher_mk = realdb.sessionmaker("a")
    human_mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    real_dispatch = discount_auto_trigger.dispatch_audit
    claimed = asyncio.Event()
    release = asyncio.Event()

    async def _hold_after_audit(*args, **kwargs):
        result = await real_dispatch(*args, **kwargs)
        claimed.set()
        await release.wait()
        return result

    human_status: list[str] = []

    async def _human_locking_read():
        async with human_mk() as human:
            row = (
                await human.execute(
                    select(DiscountOffer).where(DiscountOffer.id == offer_id).with_for_update()
                )
            ).scalar_one()
            human_status.append(row.status)
            await human.rollback()

    with patch.object(discount_auto_trigger, "dispatch_audit", _hold_after_audit):
        async with _running_sweep(
            discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const),
            release=release,
        ) as sweep:
            # Inside the manager, so a timeout HERE still releases the gate and
            # reaps the task. Previously this ran before the try/finally, so a
            # slow runner missing the 15s window orphaned a locked transaction.
            await asyncio.wait_for(claimed.wait(), timeout=15)

            human = asyncio.create_task(_human_locking_read())
            try:
                waiting = await _await_row_lock_wait(watcher_mk)
                assert waiting is not None, (
                    "the human was not blocked — the sweep holds no row lock"
                )
                assert waiting[1] in _ROW_LOCK_TYPES
                assert not human.done()
            finally:
                release.set()
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(human, timeout=30)
            captured = await sweep

    assert captured == 1
    # The human read the sweep's COMMITTED outcome, not the stale `offered`.
    assert human_status == [OFFER_STATUS_ACCEPTED]


async def test_the_claim_returns_none_once_the_row_has_moved(realdb):
    """`_claim_if_still_offered` in isolation: a row another session moved is
    not claimable, even though this session already has it in its identity
    map."""
    mk = realdb.sessionmaker("a")
    other_mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    async with mk() as db:
        # Load it — this is the stale snapshot the sweep would have.
        stale = (
            await db.execute(select(DiscountOffer).where(DiscountOffer.id == offer_id))
        ).scalar_one()
        assert stale.status == OFFER_STATUS_OFFERED

        async with other_mk() as other:
            await other.execute(
                update(DiscountOffer)
                .where(DiscountOffer.id == offer_id)
                .values(status=OFFER_STATUS_DECLINED)
            )
            await other.commit()

        assert await discount_auto_trigger._claim_if_still_offered(db, offer_id) is None


async def test_the_claim_refreshes_the_row_it_returns(realdb):
    """The `populate_existing=True` guard.

    Without it the second SELECT hands back the stale identity-mapped object
    and "re-checks" nothing — the claim would return a row whose in-memory
    fields predate the commit it exists to notice.
    """
    mk = realdb.sessionmaker("a")
    other_mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id, base="10000.00"))

    async with mk() as db:
        stale = (
            await db.execute(select(DiscountOffer).where(DiscountOffer.id == offer_id))
        ).scalar_one()
        assert stale.base_amount == Decimal("10000.00")

        async with other_mk() as other:
            await other.execute(
                update(DiscountOffer)
                .where(DiscountOffer.id == offer_id)
                .values(base_amount=Decimal("4242.42"))
            )
            await other.commit()

        claimed = await discount_auto_trigger._claim_if_still_offered(db, offer_id)
        assert claimed is not None
        assert claimed.base_amount == Decimal("4242.42")
        await db.rollback()


# ---------------------------------------------------------------------------
# Expiry takes the same claim — it is a status write too
# ---------------------------------------------------------------------------


async def test_expiry_does_not_clobber_a_decline_it_blocked_behind(realdb):
    """An `offered` offer past its `valid_until` is expired by this sweep — but
    expiry is a status write, so it claims the row like the accept path does.
    A decline committed while the sweep waits must survive as `declined`, not
    be overwritten with `expired`."""
    mk = realdb.sessionmaker("a")
    watcher_mk = realdb.sessionmaker("a")
    human_mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id, valid_until=_TODAY - timedelta(days=5)))

    async with human_mk() as human:
        await human.execute(
            select(DiscountOffer).where(DiscountOffer.id == offer_id).with_for_update()
        )
        await human.execute(
            update(DiscountOffer)
            .where(DiscountOffer.id == offer_id)
            .values(status=OFFER_STATUS_DECLINED)
        )

        async with _running_sweep(
            discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)
        ) as sweep:
            waiting = await _await_row_lock_wait(watcher_mk)
            assert waiting is not None, "the expiry path took no row lock"
            assert waiting[1] in _ROW_LOCK_TYPES

            await human.commit()
            expired = await sweep

    assert expired == 0
    assert (await _read(mk, offer_id)).status == OFFER_STATUS_DECLINED


async def test_an_uncontended_past_due_offer_still_expires(realdb):
    """The claim must not have broken ordinary expiry."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id, valid_until=_TODAY - timedelta(days=1)))

    assert await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const) == 0

    assert (await _read(mk, offer_id)).status == OFFER_STATUS_EXPIRED
    # Expiry is not an acceptance and must never be audited as one.
    assert await _audit_rows(mk, offer_id) == []


# ---------------------------------------------------------------------------
# The ROI threshold boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("threshold", "expected_captured", "expected_status"),
    [
        # `>= threshold` — exactly at the bar clears it.
        (_APR, 1, OFFER_STATUS_ACCEPTED),
        (_APR - Decimal("0.01"), 1, OFFER_STATUS_ACCEPTED),
        (_APR + Decimal("0.01"), 0, OFFER_STATUS_OFFERED),
    ],
    ids=["exactly-at", "just-under", "just-over"],
)
async def test_the_roi_threshold_boundary_is_inclusive(
    realdb, monkeypatch, threshold, expected_captured, expected_status
):
    """`FEOH_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD` is a `>=` gate. One cent of
    APR either side of the bar decides whether the org's cash leaves early, so
    the boundary is pinned rather than sampled far from it."""
    monkeypatch.setattr(settings, "discount_auto_capture_roi_threshold", float(threshold))
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    captured = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    assert captured == expected_captured
    assert (await _read(mk, offer_id)).status == expected_status


async def test_an_offer_clearing_the_threshold_but_not_the_cost_of_capital_is_skipped(realdb):
    """The gate is `worthwhile AND apr >= threshold`, and `worthwhile` is
    `apr > cost_of_capital`. An org whose cash costs more than the discount
    yields must not have that cash committed early just because a platform
    threshold was cleared."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    captured = await discount_auto_trigger._sweep_tenant(
        info.db_name, _TODAY, _resolver_of(str(_APR + Decimal("0.01")))
    )

    assert captured == 0
    assert (await _read(mk, offer_id)).status == OFFER_STATUS_OFFERED
    assert await _audit_rows(mk, offer_id) == []


async def test_the_audited_roi_records_the_threshold_the_decision_was_made_against(realdb):
    """The audit row is the only durable record of WHY the system committed the
    org's cash — it must carry the exact threshold and the ROI, as
    Decimal-strings, and no PII."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    assert await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const) == 1

    rows = await _audit_rows(mk, offer_id)
    assert len(rows) == 1
    details = rows[0].details
    assert details["threshold_pct"] == str(
        Decimal(str(settings.discount_auto_capture_roi_threshold))
    )
    assert details["roi"]["annualized_return_pct"] == str(_APR)
    assert details["roi"]["days_accelerated"] == _DAYS_ACCELERATED
    assert details["roi"]["worthwhile"] is True
    assert details["tier"] == {"days": 5, "percent": "3.00"}
    assert rows[0].actor_id is None  # system actor


# ---------------------------------------------------------------------------
# Money-path boundary — the sweep flips a status and nothing else
# ---------------------------------------------------------------------------


async def test_accepting_moves_no_money_and_does_not_advance_the_invoice(realdb):
    """The documented boundary: the sweep flags a high-ROI offer for capture,
    and a CFO-gated payment run is still the only thing that can fund it. So
    no `Payment` / `PaymentRun` / `PaymentSchedule` row may appear, the
    invoice must stay exactly where it was, and `captured_amount` must stay
    NULL — accepted is not captured."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    org_id = info.org_id

    async with mk() as db:
        inv = Invoice(
            organization_id=org_id,
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_name="Globex",
            amount=Decimal("10000.00"),
            currency="USD",
            due_date=_TODAY + timedelta(days=25),
            status=InvoiceStatus.approved,
        )
        db.add(inv)
        await db.commit()
        invoice_id = inv.id

    offer_id = await _seed(mk, _offer(org_id, invoice_id=invoice_id))

    assert await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const) == 1

    async with mk() as db:
        assert (await db.execute(select(func.count()).select_from(Payment))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(PaymentRun))).scalar_one() == 0
        assert (
            await db.execute(select(func.count()).select_from(PaymentSchedule))
        ).scalar_one() == 0
        invoice = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
        assert invoice.status == InvoiceStatus.approved
        offer = (
            await db.execute(select(DiscountOffer).where(DiscountOffer.id == offer_id))
        ).scalar_one()
        assert offer.status == OFFER_STATUS_ACCEPTED
        assert offer.captured_amount is None


async def test_a_lost_race_moves_no_money_either(realdb):
    """The skip path is also a no-money path — nothing partial is left behind
    when the sweep backs off."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    offer_id = await _seed(mk, _offer(info.org_id))

    with patch.object(
        discount_auto_trigger,
        "_resolve_due_date",
        _commit_status_midway(mk, offer_id, OFFER_STATUS_DECLINED),
    ):
        await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    async with mk() as db:
        assert (await db.execute(select(func.count()).select_from(Payment))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(PaymentRun))).scalar_one() == 0


async def test_one_lost_race_does_not_cost_the_sweep_its_other_candidates(realdb):
    """A skipped row must not abort the tick. The sweep sees two worthwhile
    offers, loses the first to a human decline, and must still accept the
    second — a `continue` that had been a `return` (or a poisoned shared
    transaction) would silently stop auto-capturing for the whole tenant."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    raced_id = await _seed(mk, _offer(info.org_id))
    other_id = await _seed(mk, _offer(info.org_id, base="20000.00"))

    with patch.object(
        discount_auto_trigger,
        "_resolve_due_date",
        _commit_status_midway(mk, raced_id, OFFER_STATUS_DECLINED),
    ):
        captured = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    assert captured == 1
    assert (await _read(mk, raced_id)).status == OFFER_STATUS_DECLINED
    assert (await _read(mk, other_id)).status == OFFER_STATUS_ACCEPTED
    assert await _audit_rows(mk, raced_id) == []
    assert len(await _audit_rows(mk, other_id)) == 1
