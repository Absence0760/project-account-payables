"""Two-phase locking discipline for ``vendor_rescreen`` and ``recurring_invoices``.

``docs/background-sweeps.md`` § Locking specifies the shape every mutating sweep
uses, and calls step 2 — re-checking the predicate under the row lock —
"correctness, not an optimisation":

  1. Select candidate **ids**, unlocked, in a deterministic order.
  2. Per id: ``db.get(Model, id, with_for_update=True)`` → re-check the predicate
     the id query used → apply → ``commit()``. Nothing to write is a
     ``rollback()``, not a hold.

Both sweeps had step 1 and the per-item commit but ran a plain
``db.get(Model, id)`` with no lock and no re-check. Each test here asserts both
halves; the re-check half is the one that fails against the pre-fix code.

Deliberately session-mocked rather than ``realdb``: the two failures being
guarded are *interleavings* (another replica commits between the id read and the
lock), and driving a real second connection into that exact window would need
either a sleep or a lock-contention race — both of which the project's
test-discipline rules forbid. Substituting the session lets the mid-tick commit
be injected at the precise instant, deterministically, in-process.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_session(candidate_ids: list, get_side_effect):
    """A session whose `execute` yields candidate ids and whose `get` is driven
    by `get_side_effect`. Records every `get` kwarg so the lock can be asserted."""
    scalars = MagicMock(all=lambda: list(candidate_ids))
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalars=lambda: scalars))
    session.get = AsyncMock(side_effect=get_side_effect)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _patch_engine(module, session):
    """Swap the module's per-tenant engine/sessionmaker for the fake session."""
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    return (
        patch.object(module, "create_async_engine", lambda *a, **k: fake_engine),
        patch.object(
            module, "async_sessionmaker", lambda *a, **k: MagicMock(return_value=session_cm)
        ),
    )


# ---------------------------------------------------------------------------
# vendor_rescreen
# ---------------------------------------------------------------------------


def _vendor(*, status="active", last_screened_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        status=status,
        last_screened_at=last_screened_at,
        screening_status="clear",
    )


@pytest.mark.asyncio
async def test_vendor_rescreen_locks_the_row_it_screens():
    """Step 2 must be a real `SELECT ... FOR UPDATE` on exactly one row."""
    from app.services import vendor_rescreen

    vendor = _vendor()
    session = _fake_session([vendor.id], get_side_effect=lambda *a, **k: vendor)
    cutoff = datetime.now(UTC) - timedelta(days=7)

    engine_patch, sessionmaker_patch = _patch_engine(vendor_rescreen, session)
    with (
        engine_patch,
        sessionmaker_patch,
        patch.object(
            vendor_rescreen,
            "screen_vendor_record",
            AsyncMock(return_value=SimpleNamespace(screening_status="clear", matched_list=None)),
        ),
    ):
        screened, _flags, failures = await vendor_rescreen._sweep_tenant("feoh_x", {}, cutoff)

    assert (screened, failures) == (1, 0)
    # `with_for_update` is what makes `Session.get` bypass the identity map and
    # emit a real row lock. Without it the "lock" is a cache read.
    assert session.get.await_args.kwargs.get("with_for_update") is True


@pytest.mark.asyncio
async def test_vendor_rescreen_skips_a_vendor_screened_between_the_id_read_and_the_lock():
    """The re-check under the lock — the half that was missing.

    Another replica's sweep (or a manual `POST /api/vendors/{id}/screen`) can
    screen the same vendor and commit while this tick is still working through
    its unlocked candidate list. Acting on the stale snapshot bills the sanctions
    provider twice for one screening event and appends a second, undeletable
    `SanctionsCheck` + `vendor.screened` audit row for it.

    Pre-fix this asserted `screened == 1` and `screen_vendor_record` was awaited:
    the sweep re-read the row (seeing the FRESH `last_screened_at`) but never
    compared it to anything.
    """
    from app.services import vendor_rescreen

    cutoff = datetime.now(UTC) - timedelta(days=7)
    stale = _vendor(last_screened_at=None)
    # The row as it reads once the lock is granted: freshly screened by someone
    # else, so it no longer satisfies the id query's predicate.
    fresh = SimpleNamespace(**{**vars(stale), "last_screened_at": datetime.now(UTC)})

    session = _fake_session([stale.id], get_side_effect=lambda *a, **k: fresh)
    screen = AsyncMock(return_value=SimpleNamespace(screening_status="clear", matched_list=None))

    engine_patch, sessionmaker_patch = _patch_engine(vendor_rescreen, session)
    with (
        engine_patch,
        sessionmaker_patch,
        patch.object(vendor_rescreen, "screen_vendor_record", screen),
    ):
        screened, flags, failures = await vendor_rescreen._sweep_tenant("feoh_x", {}, cutoff)

    assert (screened, flags, failures) == (0, 0, 0)
    screen.assert_not_awaited()  # no duplicate paid call, no duplicate trail row
    session.rollback.assert_awaited()  # the lock is released, not held to tick end
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_vendor_rescreen_skips_a_vendor_deactivated_between_the_id_read_and_the_lock():
    """`status == "active"` is part of the predicate, so it is part of the re-check.

    Re-screening a vendor an admin just retired is work nobody asked for, and a
    `match` verdict would apply a payment block to a retired supplier.
    """
    from app.services import vendor_rescreen

    cutoff = datetime.now(UTC) - timedelta(days=7)
    stale = _vendor()
    retired = SimpleNamespace(**{**vars(stale), "status": "inactive"})

    session = _fake_session([stale.id], get_side_effect=lambda *a, **k: retired)
    screen = AsyncMock(return_value=SimpleNamespace(screening_status="clear", matched_list=None))

    engine_patch, sessionmaker_patch = _patch_engine(vendor_rescreen, session)
    with (
        engine_patch,
        sessionmaker_patch,
        patch.object(vendor_rescreen, "screen_vendor_record", screen),
    ):
        screened, _flags, failures = await vendor_rescreen._sweep_tenant("feoh_x", {}, cutoff)

    assert (screened, failures) == (0, 0)
    screen.assert_not_awaited()


# ---------------------------------------------------------------------------
# recurring_invoices
# ---------------------------------------------------------------------------


def _template(*, status="active", next_run_on):
    """A FULLY generatable template — `amount` and `vendor_name` are set.

    Load-bearing: a template missing either is non-generatable, and the sweep
    routes it to `_handle_non_generatable` before it would ever call
    `generate_one`. A skip test built on such a template passes against the
    pre-fix code for the wrong reason.
    """
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        status=status,
        next_run_on=next_run_on,
        cadence="monthly",
        day_of_period=1,
        start_date=date(2024, 1, 1),
        end_date=None,
        name="Rent",
        amount=Decimal("2500.00"),
        vendor_name="Landlord Ltd",
        meta={},
    )


def _recurring_session(candidate_id, locked_row):
    """`execute` serves the candidate-id query AND the already-generated probe.

    The probe reads `.scalar_one_or_none()` — `None` means "this period has no
    invoice yet", so the sweep would proceed to generate if it got that far.
    """
    session = _fake_session([candidate_id], get_side_effect=lambda *a, **k: locked_row)
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=lambda: MagicMock(all=lambda: [candidate_id]),
            scalar_one_or_none=lambda: None,
        )
    )
    return session


@pytest.mark.asyncio
async def test_recurring_locks_the_template_it_generates_for():
    from app.services import recurring_invoices

    today = date(2026, 3, 15)
    template = _template(next_run_on=date(2026, 3, 1))
    session = _recurring_session(template.id, template)

    engine_patch, sessionmaker_patch = _patch_engine(recurring_invoices, session)
    with (
        engine_patch,
        sessionmaker_patch,
        patch.object(
            recurring_invoices, "generate_one", AsyncMock(return_value=SimpleNamespace(id="inv"))
        ),
    ):
        outcome = await recurring_invoices._sweep_tenant("feoh_x", today)

    assert outcome.generated == 1
    assert session.get.await_args.kwargs.get("with_for_update") is True


@pytest.mark.asyncio
async def test_recurring_skips_a_template_another_replica_already_advanced():
    """The re-check under the lock — and the failure `uq_invoice_recurring_period`
    does NOT cover.

    That index makes a *duplicate* invoice for one `(template, period_key)`
    impossible. The failure here produces an invoice for a period that is **not
    due yet**, on a distinct period key the index is happy to accept: replica A
    generates period P, advances `next_run_on` to P+1 and commits; replica B then
    locks the row, reads the fresh P+1 cursor and — pre-fix, taking whatever
    `next_run_on` said with no re-check — generates P+1 early. A subscription
    invoice lands in the approval queue for a month that has not started, and the
    cursor jumps to P+2 so the real P+1 tick raises nothing.

    Pre-fix this asserted `generated == 1` with `generate_one` awarded a
    `run_on` in the FUTURE relative to `today`.
    """
    from app.services import recurring_invoices

    today = date(2026, 3, 15)
    due = _template(next_run_on=date(2026, 3, 1))
    # As the row reads once the lock is granted: already generated and advanced.
    advanced = SimpleNamespace(**{**vars(due), "next_run_on": date(2026, 4, 1)})

    session = _recurring_session(due.id, advanced)
    generate = AsyncMock(return_value=SimpleNamespace(id="inv"))

    engine_patch, sessionmaker_patch = _patch_engine(recurring_invoices, session)
    with (
        engine_patch,
        sessionmaker_patch,
        patch.object(recurring_invoices, "generate_one", generate),
    ):
        outcome = await recurring_invoices._sweep_tenant("feoh_x", today)

    assert outcome.generated == 0
    generate.assert_not_awaited()  # no invoice for a period that is not due
    session.rollback.assert_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_recurring_skips_a_template_paused_between_the_id_read_and_the_lock():
    """`status == active` is part of the predicate, so it is part of the re-check.

    A pause landing mid-tick — an admin's, or the sweep's own auto-pause on
    another replica — must stop the next invoice, not race it.
    """
    from app.services import recurring_invoices

    today = date(2026, 3, 15)
    due = _template(next_run_on=date(2026, 3, 1))
    paused = SimpleNamespace(**{**vars(due), "status": recurring_invoices.STATUS_PAUSED})

    session = _recurring_session(due.id, paused)
    generate = AsyncMock(return_value=SimpleNamespace(id="inv"))

    engine_patch, sessionmaker_patch = _patch_engine(recurring_invoices, session)
    with (
        engine_patch,
        sessionmaker_patch,
        patch.object(recurring_invoices, "generate_one", generate),
    ):
        outcome = await recurring_invoices._sweep_tenant("feoh_x", today)

    assert (outcome.generated, outcome.template_failures) == (0, 0)
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_recurring_candidate_ids_are_ordered_totally():
    """Every replica must lock the same rows in the same sequence.

    `ORDER BY next_run_on` alone is only a partial order — templates sharing a
    due date come back in whatever order the plan produces, so two replicas can
    take the same two locks in opposite orders and deadlock. `id` is the
    tiebreak that makes the order total.
    """
    from app.models.recurring_invoice import RecurringInvoiceTemplate
    from app.services import recurring_invoices

    session = _fake_session([], get_side_effect=lambda *a, **k: None)
    engine_patch, sessionmaker_patch = _patch_engine(recurring_invoices, session)
    with engine_patch, sessionmaker_patch:
        await recurring_invoices._sweep_tenant("feoh_x", date(2026, 3, 15))

    compiled = str(session.execute.await_args.args[0])
    order_by = compiled.split("ORDER BY", 1)[1]
    assert RecurringInvoiceTemplate.__tablename__ + ".next_run_on" in order_by
    assert RecurringInvoiceTemplate.__tablename__ + ".id" in order_by
