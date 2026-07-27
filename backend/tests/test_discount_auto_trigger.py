"""Tests for the dynamic-discounting auto-capture sweep.

Two layers, mirroring ``test_approval_escalation`` + ``test_contract_renewal``:

  * Pure / mocked orchestration — the multi-tenant fan-out, per-tenant failure
    isolation, and the cost-of-capital resolver. No live DB.
  * Real-Postgres mutation — drives ``_sweep_tenant`` against a seeded tenant to
    prove the worthwhile→accept transition, the threshold gate, the
    money-path boundary (no Payment row), idempotency, the audit write, the
    date-window enforcement, and the ``expire_if_past`` wiring (issue #124).

Tier selection itself (``best_tier_for_date`` — highest-percent among tiers
still achievable, malformed-rung tolerance) is pinned in
``test_discount_offers.py``; this file only proves the sweep threads the
right reference date through it.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.models.discount import (
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_DECLINED,
    OFFER_STATUS_EXPIRED,
    OFFER_STATUS_OFFERED,
    DiscountOffer,
)
from app.services import discount_auto_trigger
from app.services.discount_auto_trigger import run_auto_trigger_once

_TODAY = date(2026, 1, 1)


# ---------------------------------------------------------------------------
# run_auto_trigger_once — multi-tenant fan-out (mocked)
# ---------------------------------------------------------------------------


def _fake_control_session(tenant_db_names: list[str]):
    fake_rows = [(f"org-{n}", n) for n in tenant_db_names]
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(all=lambda: fake_rows))
    cm = AsyncMock()
    cm.__aenter__.return_value = fake_session
    cm.__aexit__.return_value = None
    return MagicMock(return_value=cm)


async def test_run_once_iterates_every_tenant():
    with (
        patch.object(
            discount_auto_trigger,
            "control_session_factory",
            _fake_control_session(["feoh_a", "feoh_b", "feoh_c"]),
        ),
        patch.object(discount_auto_trigger, "_sweep_tenant", AsyncMock(return_value=2)) as sweep,
    ):
        result = await run_auto_trigger_once(today=_TODAY)

    assert result.tenants_scanned == 3
    assert result.offers_captured == 6  # 3 tenants × 2
    assert result.failures == 0
    assert sweep.await_count == 3


async def test_run_once_continues_after_one_tenant_fails():
    side_effects = [2, RuntimeError("bad json"), 1]
    with (
        patch.object(
            discount_auto_trigger,
            "control_session_factory",
            _fake_control_session(["feoh_a", "feoh_b", "feoh_c"]),
        ),
        patch.object(discount_auto_trigger, "_sweep_tenant", AsyncMock(side_effect=side_effects)),
    ):
        result = await run_auto_trigger_once(today=_TODAY)

    assert result.tenants_scanned == 3
    assert result.offers_captured == 3  # 2 + 1; the failure contributed 0
    assert result.failures == 1


# ---------------------------------------------------------------------------
# _sweep_tenant — real Postgres mutation
# ---------------------------------------------------------------------------


def _make_offer(
    org_id,
    *,
    tiers,
    base="10000.00",
    valid_until=None,
    valid_from=None,
    status=OFFER_STATUS_OFFERED,
):
    return DiscountOffer(
        id=uuid.uuid4(),
        organization_id=org_id,
        scope="invoice",
        invoice_id=None,
        vendor_id=None,
        source="supplier",
        status=status,
        tiers=tiers,
        base_amount=Decimal(base),
        currency="USD",
        valid_until=valid_until,
        valid_from=valid_from,
    )


async def _resolver_const(_org_id, value=Decimal("8.00")):
    return value


@pytest.mark.asyncio
async def test_sweep_accepts_worthwhile_offer_and_audits(realdb):
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    db_name = info.db_name
    org_id = info.org_id

    # 3% for paying 25 days early → APR ~45% (well above the 12% threshold).
    offer = _make_offer(
        org_id,
        tiers=[{"days": 5, "percent": "3.00"}, {"days": 10, "percent": "1.00"}],
        valid_until=date(2026, 1, 26),
    )
    async with mk() as db:
        db.add(offer)
        await db.commit()
        offer_id = offer.id

    captured = await discount_auto_trigger._sweep_tenant(db_name, _TODAY, _resolver_const)
    assert captured == 1

    from sqlalchemy import select

    from app.models.workflow import AuditLog

    async with mk() as db:
        row = (
            await db.execute(select(DiscountOffer).where(DiscountOffer.id == offer_id))
        ).scalar_one()
        assert row.status == OFFER_STATUS_ACCEPTED
        assert row.accepted_at is not None
        # Best (highest %) tier was chosen.
        assert row.accepted_tier == {"days": 5, "percent": "3.00"}

        audit = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "discount_offer.auto_accepted",
                    AuditLog.entity_id == offer_id,
                )
            )
        ).scalar_one()
        assert audit.entity_type == "discount_offer"
        # ROI carried as Decimal-strings; no PII.
        assert audit.details["roi"]["worthwhile"] is True
        assert "savings" in audit.details["roi"]


@pytest.mark.asyncio
async def test_sweep_skips_below_threshold(realdb):
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    db_name = info.db_name

    # 1% for paying ~10 days early → APR ~36%? No: 1/(99)*365/10*100 ≈ 36.9%.
    # To land clearly *below* the 12% threshold, accelerate far out: 0.5% over
    # 300 days → APR ~0.6%.
    offer = _make_offer(
        info.org_id,
        tiers=[{"days": 5, "percent": "0.50"}],
        valid_until=date(2026, 10, 28),  # ~300 days out
    )
    async with mk() as db:
        db.add(offer)
        await db.commit()
        offer_id = offer.id

    captured = await discount_auto_trigger._sweep_tenant(db_name, _TODAY, _resolver_const)
    assert captured == 0

    from sqlalchemy import select

    async with mk() as db:
        row = (
            await db.execute(select(DiscountOffer).where(DiscountOffer.id == offer_id))
        ).scalar_one()
        assert row.status == OFFER_STATUS_OFFERED  # untouched


@pytest.mark.asyncio
async def test_sweep_ignores_declined_and_expires_past_valid_until(realdb):
    """A declined offer is untouched. An `offered` offer whose `valid_until`
    has already passed used to sit there forever (`expire_if_past` was never
    invoked by any sweep — issue #124) — the sweep must now flip it to
    `expired` instead of leaving it `offered`."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    db_name = info.db_name
    org_id = info.org_id

    declined = _make_offer(
        org_id,
        tiers=[{"days": 5, "percent": "3.00"}],
        valid_until=date(2026, 1, 26),
        status=OFFER_STATUS_DECLINED,
    )
    expired = _make_offer(
        org_id,
        tiers=[{"days": 5, "percent": "3.00"}],
        valid_until=date(2025, 12, 1),  # already past
    )
    async with mk() as db:
        db.add_all([declined, expired])
        await db.commit()
        declined_id, expired_id = declined.id, expired.id

    captured = await discount_auto_trigger._sweep_tenant(db_name, _TODAY, _resolver_const)
    assert captured == 0

    from sqlalchemy import select

    async with mk() as db:
        d = (
            await db.execute(select(DiscountOffer).where(DiscountOffer.id == declined_id))
        ).scalar_one()
        e = (
            await db.execute(select(DiscountOffer).where(DiscountOffer.id == expired_id))
        ).scalar_one()
        assert d.status == OFFER_STATUS_DECLINED
        assert e.status == OFFER_STATUS_EXPIRED


@pytest.mark.asyncio
async def test_sweep_does_not_auto_accept_a_tier_whose_window_has_closed(realdb):
    """Reproduces the issue's exact failure scenario: an offer opened 20 days
    ago with a 5-day/3% and 10-day/2% sliding scale, still within its own
    `valid_until` (+10 days out). Both tiers' REAL deadlines (measured from
    `valid_from`) are long past — the sweep must not auto-accept using
    today-as-reference, which would make every tier look perpetually
    achievable."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    db_name = info.db_name

    offer = _make_offer(
        info.org_id,
        tiers=[{"days": 5, "percent": "3.00"}, {"days": 10, "percent": "2.00"}],
        valid_from=_TODAY - timedelta(days=20),
        valid_until=_TODAY + timedelta(days=10),
    )
    async with mk() as db:
        db.add(offer)
        await db.commit()
        offer_id = offer.id

    captured = await discount_auto_trigger._sweep_tenant(db_name, _TODAY, _resolver_const)
    assert captured == 0

    from sqlalchemy import select

    async with mk() as db:
        row = (
            await db.execute(select(DiscountOffer).where(DiscountOffer.id == offer_id))
        ).scalar_one()
        assert row.status == OFFER_STATUS_OFFERED  # not auto-accepted; not expired either
        assert row.accepted_tier is None


@pytest.mark.asyncio
async def test_sweep_is_idempotent_no_double_accept(realdb):
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    db_name = info.db_name

    offer = _make_offer(
        info.org_id,
        tiers=[{"days": 5, "percent": "3.00"}],
        valid_until=date(2026, 1, 26),
    )
    async with mk() as db:
        db.add(offer)
        await db.commit()
        offer_id = offer.id

    first = await discount_auto_trigger._sweep_tenant(db_name, _TODAY, _resolver_const)
    second = await discount_auto_trigger._sweep_tenant(db_name, _TODAY, _resolver_const)
    assert first == 1
    assert second == 0  # status guard dedupes

    from sqlalchemy import select

    from app.models.workflow import AuditLog

    async with mk() as db:
        audits = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.action == "discount_offer.auto_accepted",
                        AuditLog.entity_id == offer_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) == 1  # exactly one accept audited, not two


@pytest.mark.asyncio
async def test_sweep_never_creates_a_payment(realdb):
    """Money-path boundary: the sweep accepts the offer but moves no money."""
    mk = realdb.sessionmaker("a")
    info = realdb.info("a")
    db_name = info.db_name

    offer = _make_offer(
        info.org_id,
        tiers=[{"days": 5, "percent": "3.00"}],
        valid_until=date(2026, 1, 26),
    )
    async with mk() as db:
        db.add(offer)
        await db.commit()

    await discount_auto_trigger._sweep_tenant(db_name, _TODAY, _resolver_const)

    from sqlalchemy import func, select

    from app.models.payment import Payment, PaymentRun

    async with mk() as db:
        payments = (await db.execute(select(func.count()).select_from(Payment))).scalar_one()
        runs = (await db.execute(select(func.count()).select_from(PaymentRun))).scalar_one()
        assert payments == 0
        assert runs == 0


# ---------------------------------------------------------------------------
# cost-of-capital resolver (mocked control plane)
# ---------------------------------------------------------------------------


async def test_resolver_uses_org_override_then_platform_default():
    org_id = uuid.uuid4()

    def _ctrl(settings_value):
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: settings_value)
        )
        cm = AsyncMock()
        cm.__aenter__.return_value = fake_session
        cm.__aexit__.return_value = None
        return MagicMock(return_value=cm)

    # Override present → used.
    with patch.object(
        discount_auto_trigger,
        "control_session_factory",
        _ctrl({"discounting": {"cost_of_capital_pct": 15.5}}),
    ):
        assert await discount_auto_trigger._resolve_cost_of_capital(org_id) == Decimal("15.5")

    # No discounting block → platform default.
    with patch.object(discount_auto_trigger, "control_session_factory", _ctrl({})):
        got = await discount_auto_trigger._resolve_cost_of_capital(org_id)
        assert got == Decimal(str(settings.discount_cost_of_capital_pct))
