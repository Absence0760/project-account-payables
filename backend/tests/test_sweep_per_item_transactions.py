"""Per-item transactions in the two sweeps that still ran one per tenant.

``discount_auto_trigger`` and ``contract_renewal`` both loaded their candidates,
mutated them in a loop and committed **once** at the end of the tenant. One bad
row therefore discarded the entire tick's work — and in ``contract_renewal``'s
case across two independent passes, so a raise while expiring a contract rolled
back every ``renewal_alert_sent_at`` the alert pass had just stamped, and vice
versa. Because the causes are deterministic (a malformed row does not heal
itself), that tenant then made **zero** forward progress on every subsequent
tick, forever, while the discarded per-tenant counter reported nothing.

These tests pin the fix — the commit-per-item shape documented in
``backend/docs/background-sweeps.md`` § Locking and already used by
``vendor_rescreen`` / ``recurring_invoices`` / ``approval_escalation``:

1. one poisoned row raises, and the OTHER rows in the same tick still commit;
2. the failure is counted in a ``*_failures`` field, which
   ``sweep_health.failure_count`` sums, so the tick reports ``partial`` and —
   past the streak — ``degraded`` rather than ``ok``;
3. for ``contract_renewal``, a failure in one pass leaves the other pass's
   committed work alone.

Every poisoned row is given a **lower** id than its healthy sibling, because
both sweeps order their candidates by id: a poison row sorted last would let
the pre-fix implementation pass by accident.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.contract import Contract, ContractStatus, ContractType
from app.models.discount import (
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_OFFERED,
    DiscountOffer,
)
from app.models.vendor import Vendor
from app.services import contract_renewal, discount_auto_trigger, sweep_health

_TODAY = date(2026, 1, 1)

# Deterministic, ordered ids — `_sweep_tenant` selects candidates `ORDER BY id`,
# so LOW sorts first and is the row we poison.
_LOW = uuid.UUID("00000000-0000-4000-8000-000000000001")
_HIGH = uuid.UUID("00000000-0000-4000-8000-000000000002")


class _Poison(RuntimeError):
    """Deterministic per-row failure — the shape a malformed row produces."""


@pytest.fixture(autouse=True)
def _clean_sweep_registry():
    """The health registry is process-global module state; keep it ours."""
    sweep_health.reset()
    yield
    sweep_health.reset()


def _degraded_after_repeated_ticks(name: str, result: object) -> str:
    """Feed the same result through the streak and report the aggregate state.

    Uses the real ``run_succeeded`` → ``snapshot_of`` → ``overall_state`` chain
    (not a re-implementation), scoped to this one sweep's row so the other
    thirteen never-started sweeps can't decide the verdict.
    """
    for _ in range(max(sweep_health.alert_streak(), 1)):
        sweep_health.run_succeeded(name, result)
    return sweep_health.overall_state([sweep_health.snapshot_of(name)])


# ---------------------------------------------------------------------------
# discount_auto_trigger
# ---------------------------------------------------------------------------


def _offer(org_id: uuid.UUID, offer_id: uuid.UUID, *, base: str) -> DiscountOffer:
    """One worthwhile `offered` offer: 3% for paying ~25 days early ≈ 45% APR,
    comfortably over the 12% auto-capture threshold."""
    return DiscountOffer(
        id=offer_id,
        organization_id=org_id,
        scope="invoice",
        source="supplier",
        status=OFFER_STATUS_OFFERED,
        tiers=[{"days": 5, "percent": "3.00"}],
        base_amount=Decimal(base),
        currency="USD",
        valid_from=_TODAY,
        valid_until=date(2026, 1, 26),
    )


async def _resolver_const(_org_id):
    return Decimal("8.00")


async def _seed_offers(mk, org_id) -> None:
    async with mk() as db:
        db.add(_offer(org_id, _LOW, base="10000.00"))
        db.add(_offer(org_id, _HIGH, base="20000.00"))
        await db.commit()


def _poison_offer(target: uuid.UUID):
    """Make one offer's due-date resolution raise, leaving the rest healthy."""
    real = discount_auto_trigger._resolve_due_date

    async def _fake(db, offer):
        if offer.id == target:
            raise _Poison("malformed row")
        return await real(db, offer)

    return patch.object(discount_auto_trigger, "_resolve_due_date", _fake)


async def _offer_status(mk, offer_id: uuid.UUID) -> str:
    async with mk() as db:
        return (
            await db.execute(select(DiscountOffer.status).where(DiscountOffer.id == offer_id))
        ).scalar_one()


@pytest.mark.asyncio
async def test_one_poisoned_offer_does_not_discard_the_ticks_other_acceptances(realdb):
    """Pre-fix the raise escaped `_sweep_tenant` entirely: the single
    end-of-loop commit never ran, so the healthy offer stayed `offered` and the
    tenant re-poisoned itself on the identical row every tick, forever."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    await _seed_offers(mk, info.org_id)

    with _poison_offer(_LOW):
        outcome = await discount_auto_trigger._sweep_tenant(info.db_name, _TODAY, _resolver_const)

    assert outcome.captured == 1
    assert outcome.offer_failures == 1
    # The healthy offer's acceptance is COMMITTED, not merely in-session.
    assert await _offer_status(mk, _HIGH) == OFFER_STATUS_ACCEPTED
    # The poisoned one is untouched — never half-accepted.
    assert await _offer_status(mk, _LOW) == OFFER_STATUS_OFFERED


@pytest.mark.asyncio
async def test_a_failed_offer_is_counted_and_degrades_the_sweeps_health(realdb):
    """The counter's NAME is load-bearing: `sweep_health.failure_count` sums
    `failures` plus any `*_failures` field. Swallowing the offer without one
    would report a perfectly healthy tick that accepted nothing."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    await _seed_offers(mk, info.org_id)

    with _poison_offer(_LOW):
        result = await discount_auto_trigger.run_auto_trigger_once(today=_TODAY)

    assert result.offer_failures == 1
    assert result.failures == 0  # the TENANT sweep completed; one offer did not
    counts = sweep_health.extract_counts(result)
    assert counts["offer_failures"] == 1
    assert sweep_health.failure_count(counts) == 1

    health = sweep_health.run_succeeded(sweep_health.SWEEP_DISCOUNT_AUTO_TRIGGER, result)
    assert health.last_outcome == sweep_health.OUTCOME_PARTIAL
    assert (
        _degraded_after_repeated_ticks(sweep_health.SWEEP_DISCOUNT_AUTO_TRIGGER, result)
        == "degraded"
    )


# ---------------------------------------------------------------------------
# contract_renewal
# ---------------------------------------------------------------------------


def _contract(
    org_id: uuid.UUID,
    vendor_id: uuid.UUID,
    contract_id: uuid.UUID,
    *,
    number: str,
    end_date: date,
    owner_user_id: uuid.UUID,
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
        renewal_notice_days=30,
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


def _poison_alert(number: str):
    """Make ONE contract's alert rendering raise — the alert pass's per-row work."""
    real = contract_renewal.render_contract_renewal

    def _fake(*, contract_number, **kwargs):
        if contract_number == number:
            raise _Poison("malformed row")
        return real(contract_number=contract_number, **kwargs)

    return patch.object(contract_renewal, "render_contract_renewal", _fake)


def _poison_expiry(number: str):
    """Make ONE contract's `contract.expired` audit write raise — the expiry
    pass's per-row work."""
    real = contract_renewal.dispatch_audit

    async def _fake(db, **kwargs):
        if (kwargs.get("details") or {}).get("contract_number") == number:
            raise _Poison("audit write refused")
        return await real(db, **kwargs)

    return patch.object(contract_renewal, "dispatch_audit", _fake)


async def _reload(mk, contract_id: uuid.UUID) -> Contract:
    async with mk() as db:
        return (await db.execute(select(Contract).where(Contract.id == contract_id))).scalar_one()


@pytest.mark.asyncio
async def test_one_poisoned_alert_does_not_discard_the_ticks_other_alerts(realdb):
    """Pre-fix the raise escaped `_sweep_tenant`, so the healthy contract's
    `renewal_alert_sent_at` was rolled back with it and its AP manager was
    never told — on this tick or any later one, since the poison row aborted
    the loop at the same place every time."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    owner = info.users["ap_manager"]
    await _seed_contracts(
        mk,
        info.org_id,
        {
            "contract_id": _LOW,
            "number": "POISON-001",
            "end_date": _TODAY + timedelta(days=10),
            "owner_user_id": owner,
        },
        {
            "contract_id": _HIGH,
            "number": "HEALTHY-001",
            "end_date": _TODAY + timedelta(days=10),
            "owner_user_id": owner,
        },
    )

    with _poison_alert("POISON-001"):
        outcome = await contract_renewal._sweep_tenant(info.db_name, _TODAY)

    assert outcome.alerts_sent == 1
    assert outcome.contract_failures == 1
    assert (await _reload(mk, _HIGH)).renewal_alert_sent_at is not None
    # The poisoned contract must NOT be marked alerted — the marker suppresses
    # the warning for the contract's whole remaining term.
    assert (await _reload(mk, _LOW)).renewal_alert_sent_at is None


@pytest.mark.asyncio
async def test_one_poisoned_expiry_does_not_discard_the_ticks_other_expiries(realdb):
    """Same property for the second pass: expiring one over-term contract must
    not depend on every other over-term contract expiring cleanly."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    owner = info.users["ap_manager"]
    already = datetime.now(UTC)
    await _seed_contracts(
        mk,
        info.org_id,
        {
            "contract_id": _LOW,
            "number": "POISON-002",
            "end_date": _TODAY - timedelta(days=5),
            "owner_user_id": owner,
            "alert_sent": already,
        },
        {
            "contract_id": _HIGH,
            "number": "HEALTHY-002",
            "end_date": _TODAY - timedelta(days=5),
            "owner_user_id": owner,
            "alert_sent": already,
        },
    )

    with _poison_expiry("POISON-002"):
        outcome = await contract_renewal._sweep_tenant(info.db_name, _TODAY)

    assert outcome.contracts_expired == 1
    assert outcome.contract_failures == 1
    assert (await _reload(mk, _HIGH)).status == ContractStatus.expired
    assert (await _reload(mk, _LOW)).status == ContractStatus.active


@pytest.mark.asyncio
async def test_a_failure_in_the_expiry_pass_leaves_the_alert_passs_stamps(realdb):
    """The two passes are independent controls and must not be able to undo one
    another. Pre-fix they shared one transaction and one commit, so a raise
    while EXPIRING a contract silently discarded every renewal alert already
    sent on that tick — the emails went out, the markers did not, and the next
    tick re-sent them all (until it hit the same poison row again)."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    owner = info.users["ap_manager"]
    await _seed_contracts(
        mk,
        info.org_id,
        {
            # Alert pass only — already-alerted rows are excluded from it.
            "contract_id": _LOW,
            "number": "ALERTED-003",
            "end_date": _TODAY + timedelta(days=10),
            "owner_user_id": owner,
        },
        {
            # Expiry pass only, and it is the one that raises.
            "contract_id": _HIGH,
            "number": "POISON-003",
            "end_date": _TODAY - timedelta(days=5),
            "owner_user_id": owner,
            "alert_sent": datetime.now(UTC),
        },
    )

    with _poison_expiry("POISON-003"):
        outcome = await contract_renewal._sweep_tenant(info.db_name, _TODAY)

    assert outcome.alerts_sent == 1
    assert outcome.contracts_expired == 0
    assert outcome.contract_failures == 1
    # The alert pass's committed work survives the other pass's failure.
    assert (await _reload(mk, _LOW)).renewal_alert_sent_at is not None
    assert (await _reload(mk, _HIGH)).status == ContractStatus.active


@pytest.mark.asyncio
async def test_a_failed_contract_is_counted_and_degrades_the_sweeps_health(realdb):
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    owner = info.users["ap_manager"]
    await _seed_contracts(
        mk,
        info.org_id,
        {
            "contract_id": _LOW,
            "number": "POISON-004",
            "end_date": _TODAY + timedelta(days=10),
            "owner_user_id": owner,
        },
        {
            "contract_id": _HIGH,
            "number": "HEALTHY-004",
            "end_date": _TODAY + timedelta(days=10),
            "owner_user_id": owner,
        },
    )

    with _poison_alert("POISON-004"):
        result = await contract_renewal.notify_renewals_once(today=_TODAY)

    assert result.contract_failures == 1
    assert result.failures == 0  # the TENANT sweep completed; one contract did not
    counts = sweep_health.extract_counts(result)
    assert counts["contract_failures"] == 1
    assert sweep_health.failure_count(counts) == 1

    health = sweep_health.run_succeeded(sweep_health.SWEEP_CONTRACT_RENEWAL, result)
    assert health.last_outcome == sweep_health.OUTCOME_PARTIAL
    assert _degraded_after_repeated_ticks(sweep_health.SWEEP_CONTRACT_RENEWAL, result) == "degraded"
