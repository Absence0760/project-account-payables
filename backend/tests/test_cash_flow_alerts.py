"""Projected cash-shortfall alert sweep (`services.cash_flow_alerts`).

Three layers, mirroring `test_discount_auto_trigger` / `test_contract_renewal`:

  * **Pure** — `project_shortfall` reduces the breach list to the one period
    worth alerting on.
  * **Mocked orchestration** — the multi-org fan-out, per-org failure
    isolation, the opt-in (no threshold → skipped), the alerted-period dedupe,
    the re-arm on resolution, and the recipient-less retry.
  * **Real Postgres** — `_project_tenant` against a seeded tenant, proving the
    breach comes from real invoices AND that the sweep mutates nothing on the
    money path (no Payment / PaymentRun, no invoice status change).

The alert's audience is pinned to the copilot's own read gate by a drift guard
below — "who may see this org's cash" must have exactly one answer.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import cash_flow_alerts
from app.services.cash_flow_alerts import (
    ALERT_ROLES,
    ShortfallProjection,
    project_shortfall,
    run_shortfall_alerts_once,
)

_TODAY = date(2026, 1, 1)


# ---------------------------------------------------------------------------
# project_shortfall — pure
# ---------------------------------------------------------------------------


def test_project_shortfall_clear_when_no_breaches():
    p = project_shortfall([], threshold=Decimal("100"), currency="USD")
    assert p.period is None
    assert p.breach_count == 0
    assert p.threshold == Decimal("100")


def test_project_shortfall_picks_the_earliest_breach():
    """The first breaching period is the deadline the finance leader is working
    against — a later, deeper one is not the headline."""
    breaches = [
        {"period": "2026-W05", "closing": Decimal("-200.00"), "shortfall": Decimal("300.00")},
        {"period": "2026-W09", "closing": Decimal("-9000.00"), "shortfall": Decimal("9100.00")},
    ]
    p = project_shortfall(breaches, threshold=Decimal("100.00"), currency="EUR")
    assert p.period == "2026-W05"
    assert p.closing == Decimal("-200.00")
    assert p.shortfall == Decimal("300.00")
    # The full extent still rides along so the message can say how widespread.
    assert p.breach_count == 2
    assert p.currency == "EUR"


def test_project_shortfall_money_stays_decimal():
    p = project_shortfall(
        [{"period": "2026-W05", "closing": "-1.50", "shortfall": "2.50"}],
        threshold=Decimal("1.00"),
        currency="USD",
    )
    assert isinstance(p.closing, Decimal) and p.closing == Decimal("-1.50")
    assert isinstance(p.shortfall, Decimal) and p.shortfall == Decimal("2.50")


# ---------------------------------------------------------------------------
# Audience — drift guard against the copilot's own read gate
# ---------------------------------------------------------------------------


def test_alert_audience_matches_the_copilot_read_gate():
    """The push surface and the pull surface must agree on who may see org cash.
    If `COPILOT_ROLES` changes, this fails until `ALERT_ROLES` follows."""
    from app.api.cash_flow import COPILOT_ROLES

    assert set(ALERT_ROLES) == set(COPILOT_ROLES)


# ---------------------------------------------------------------------------
# run_shortfall_alerts_once — multi-org fan-out (mocked)
# ---------------------------------------------------------------------------


def _fake_control_session(orgs: list[tuple]):
    """orgs: [(org_id, db_name, settings_dict), ...]"""
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(all=lambda: orgs))
    cm = AsyncMock()
    cm.__aenter__.return_value = fake_session
    cm.__aexit__.return_value = None
    return MagicMock(return_value=cm)


def _org(name: str, settings: dict | None = None) -> tuple:
    return (uuid.uuid4(), f"feoh_{name}", settings if settings is not None else {})


def _breaching(period="2026-W05") -> ShortfallProjection:
    return ShortfallProjection(
        period=period,
        closing=Decimal("-500.00"),
        shortfall=Decimal("1500.00"),
        threshold=Decimal("1000.00"),
        breach_count=1,
        currency="USD",
    )


async def test_sweep_iterates_every_org_and_alerts_each():
    orgs = [_org("a"), _org("b"), _org("c")]
    with (
        patch.object(cash_flow_alerts, "control_session_factory", _fake_control_session(orgs)),
        patch.object(cash_flow_alerts, "_project_tenant", AsyncMock(return_value=_breaching())),
        patch.object(cash_flow_alerts, "_notify_tenant", AsyncMock(return_value=True)) as notify,
        patch.object(cash_flow_alerts, "_store_marker", AsyncMock()) as marker,
    ):
        result = await run_shortfall_alerts_once(today=_TODAY)

    assert result.tenants_scanned == 3
    assert result.alerts_sent == 3
    assert result.failures == 0
    assert notify.await_count == 3
    assert marker.await_count == 3
    # The alerted period is what gets persisted — that's the dedupe key.
    assert marker.await_args.kwargs["period"] == "2026-W05"
    assert marker.await_args.kwargs["sent_on"] == _TODAY.isoformat()


async def test_sweep_continues_after_one_org_fails():
    orgs = [_org("a"), _org("b"), _org("c")]
    with (
        patch.object(cash_flow_alerts, "control_session_factory", _fake_control_session(orgs)),
        patch.object(
            cash_flow_alerts,
            "_project_tenant",
            AsyncMock(side_effect=[_breaching(), RuntimeError("bad settings"), _breaching()]),
        ),
        patch.object(cash_flow_alerts, "_notify_tenant", AsyncMock(return_value=True)),
        patch.object(cash_flow_alerts, "_store_marker", AsyncMock()),
    ):
        result = await run_shortfall_alerts_once(today=_TODAY)

    assert result.tenants_scanned == 3
    assert result.alerts_sent == 2
    assert result.failures == 1


async def test_org_without_a_threshold_is_skipped_entirely():
    """No persisted `min_balance_threshold` = no opt-in. Nothing is sent and the
    org's settings are not rewritten."""
    with (
        patch.object(
            cash_flow_alerts, "control_session_factory", _fake_control_session([_org("a")])
        ),
        patch.object(cash_flow_alerts, "_project_tenant", AsyncMock(return_value=None)),
        patch.object(cash_flow_alerts, "_notify_tenant", AsyncMock()) as notify,
        patch.object(cash_flow_alerts, "_store_marker", AsyncMock()) as marker,
    ):
        result = await run_shortfall_alerts_once(today=_TODAY)

    assert result.alerts_sent == 0
    assert notify.await_count == 0
    assert marker.await_count == 0


async def test_same_period_is_not_re_alerted():
    """A standing shortfall is announced once, not every tick — the persisted
    marker is the dedupe (the role `renewal_alert_sent_at` plays in
    `contract_renewal`)."""
    already = {"cashflow": {"shortfall_alert": {"period": "2026-W05"}}}
    with (
        patch.object(
            cash_flow_alerts,
            "control_session_factory",
            _fake_control_session([_org("a", already)]),
        ),
        patch.object(
            cash_flow_alerts, "_project_tenant", AsyncMock(return_value=_breaching("2026-W05"))
        ),
        patch.object(cash_flow_alerts, "_notify_tenant", AsyncMock()) as notify,
        patch.object(cash_flow_alerts, "_store_marker", AsyncMock()) as marker,
    ):
        result = await run_shortfall_alerts_once(today=_TODAY)

    assert result.alerts_sent == 0
    assert notify.await_count == 0
    assert marker.await_count == 0


async def test_shortfall_moving_to_a_new_period_re_alerts():
    """The projection changed — that IS news, so it is announced again."""
    already = {"cashflow": {"shortfall_alert": {"period": "2026-W05"}}}
    with (
        patch.object(
            cash_flow_alerts,
            "control_session_factory",
            _fake_control_session([_org("a", already)]),
        ),
        patch.object(
            cash_flow_alerts, "_project_tenant", AsyncMock(return_value=_breaching("2026-W02"))
        ),
        patch.object(cash_flow_alerts, "_notify_tenant", AsyncMock(return_value=True)),
        patch.object(cash_flow_alerts, "_store_marker", AsyncMock()) as marker,
    ):
        result = await run_shortfall_alerts_once(today=_TODAY)

    assert result.alerts_sent == 1
    assert marker.await_args.kwargs["period"] == "2026-W02"


async def test_resolved_shortfall_clears_the_marker_without_notifying():
    """ "You're fine now" isn't worth an email — but the marker must clear so a
    recurrence is announced again."""
    already = {"cashflow": {"shortfall_alert": {"period": "2026-W05"}}}
    clear = ShortfallProjection(period=None, threshold=Decimal("1000.00"), currency="USD")
    with (
        patch.object(
            cash_flow_alerts,
            "control_session_factory",
            _fake_control_session([_org("a", already)]),
        ),
        patch.object(cash_flow_alerts, "_project_tenant", AsyncMock(return_value=clear)),
        patch.object(cash_flow_alerts, "_notify_tenant", AsyncMock()) as notify,
        patch.object(cash_flow_alerts, "_store_marker", AsyncMock()) as marker,
    ):
        result = await run_shortfall_alerts_once(today=_TODAY)

    assert result.alerts_sent == 0
    assert notify.await_count == 0
    assert marker.await_args.kwargs["period"] is None


async def test_already_clear_org_is_left_alone():
    """No breach and no stored marker → nothing to say and nothing to write."""
    clear = ShortfallProjection(period=None, threshold=Decimal("1000.00"), currency="USD")
    with (
        patch.object(
            cash_flow_alerts, "control_session_factory", _fake_control_session([_org("a")])
        ),
        patch.object(cash_flow_alerts, "_project_tenant", AsyncMock(return_value=clear)),
        patch.object(cash_flow_alerts, "_notify_tenant", AsyncMock()),
        patch.object(cash_flow_alerts, "_store_marker", AsyncMock()) as marker,
    ):
        await run_shortfall_alerts_once(today=_TODAY)

    assert marker.await_count == 0


async def test_no_recipients_leaves_the_marker_unwritten_so_it_retries():
    """An org with no finance leaders yet must not have its alert silently
    consumed — leave the marker unset so a later sweep still fires."""
    with (
        patch.object(
            cash_flow_alerts, "control_session_factory", _fake_control_session([_org("a")])
        ),
        patch.object(cash_flow_alerts, "_project_tenant", AsyncMock(return_value=_breaching())),
        patch.object(cash_flow_alerts, "_notify_tenant", AsyncMock(return_value=False)),
        patch.object(cash_flow_alerts, "_store_marker", AsyncMock()) as marker,
    ):
        result = await run_shortfall_alerts_once(today=_TODAY)

    assert result.alerts_sent == 0
    assert marker.await_count == 0


# ---------------------------------------------------------------------------
# _notify_tenant — recipients + event shape (mocked dispatch)
# ---------------------------------------------------------------------------


def _fake_engine_and_sessionmaker():
    """Stand-ins for the per-tenant engine + session factory `_notify_tenant`
    builds — `dispose()` and `commit()` are awaited, so both need AsyncMocks."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    session = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = session
    cm.__aexit__.return_value = None
    return MagicMock(return_value=engine), MagicMock(return_value=MagicMock(return_value=cm))


async def test_notify_fans_out_to_every_finance_leader_role():
    org_id = uuid.uuid4()
    seen_roles: list[str] = []

    async def _roles(_org, role):
        seen_roles.append(role)
        return [uuid.uuid4()]

    fake_engine, fake_sessionmaker = _fake_engine_and_sessionmaker()
    with (
        patch.object(cash_flow_alerts, "resolve_role_user_ids", AsyncMock(side_effect=_roles)),
        patch.object(cash_flow_alerts, "notify_event", AsyncMock()) as notify,
        patch.object(cash_flow_alerts, "create_async_engine", fake_engine),
        patch.object(cash_flow_alerts, "async_sessionmaker", fake_sessionmaker),
    ):
        sent = await cash_flow_alerts._notify_tenant(
            org_id=org_id, db_name="feoh_x", projection=_breaching()
        )

    assert sent is True
    assert set(seen_roles) == set(ALERT_ROLES)
    kwargs = notify.await_args.kwargs
    assert kwargs["event_type"] == "cash_shortfall_projected"
    # The alert is about the org's whole position — no record to point at.
    assert kwargs["entity_type"] == "cash_position"
    assert kwargs["entity_id"] is None
    assert len(kwargs["recipient_user_ids"]) == 3
    # PII-free, exact money straight off the Decimal.
    assert "1,500.00" in kwargs["rendered"].body_text
    assert "2026-W05" in kwargs["rendered"].title


async def test_notify_returns_false_when_org_has_no_finance_leaders():
    with (
        patch.object(cash_flow_alerts, "resolve_role_user_ids", AsyncMock(return_value=[])),
        patch.object(cash_flow_alerts, "notify_event", AsyncMock()) as notify,
    ):
        sent = await cash_flow_alerts._notify_tenant(
            org_id=uuid.uuid4(), db_name="feoh_x", projection=_breaching()
        )

    assert sent is False
    assert notify.await_count == 0


# ---------------------------------------------------------------------------
# _project_tenant — real Postgres
# ---------------------------------------------------------------------------


async def _default_entity_id(session, org_id):
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT id FROM entities WHERE organization_id = :o AND is_default"),
            {"o": org_id},
        )
    ).first()
    return row[0]


async def _seed_invoice(session, org_id, entity_id, *, number, amount, due_date):
    from app.models.invoice import Invoice

    inv = Invoice(
        id=uuid.uuid4(),
        organization_id=org_id,
        entity_id=entity_id,
        invoice_number=number,
        vendor_name="ShortfallCo",
        amount=Decimal(str(amount)),
        currency="USD",
        status="approved",
        invoice_date=date.today(),
        due_date=due_date,
    )
    session.add(inv)
    return inv


@pytest.mark.asyncio
async def test_project_tenant_detects_a_real_breach(realdb):
    """A seeded outflow that takes the projected balance below the org's
    persisted minimum is detected from real rows, in the reporting currency."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    async with mk() as db:
        ent = await _default_entity_id(db, info.org_id)
        await _seed_invoice(
            db,
            info.org_id,
            ent,
            number="SF-1",
            amount="9000.00",
            due_date=date.today() + timedelta(days=10),
        )
        await db.commit()

    projection = await cash_flow_alerts._project_tenant(
        db_name=info.db_name,
        org_settings={
            "cashflow": {"opening_balance": "1000.00", "min_balance_threshold": "0.00"},
            "reporting_currency": "USD",
        },
        ref_today=date.today(),
    )

    assert projection is not None
    assert projection.period is not None, "expected a breaching period"
    # 1000 opening − 9000 outflow → −8000, i.e. 8000 below a zero minimum.
    assert projection.closing == Decimal("-8000.00")
    assert projection.shortfall == Decimal("8000.00")
    assert projection.currency == "USD"
    assert isinstance(projection.shortfall, Decimal)


@pytest.mark.asyncio
async def test_project_tenant_returns_none_without_a_threshold(realdb):
    info = realdb.info("a")
    projection = await cash_flow_alerts._project_tenant(
        db_name=info.db_name,
        org_settings={"cashflow": {"opening_balance": "1000.00"}},
        ref_today=date.today(),
    )
    assert projection is None


@pytest.mark.asyncio
async def test_project_tenant_moves_no_money(realdb):
    """The money-path boundary: projecting a shortfall creates no Payment /
    PaymentRun and changes no invoice status."""
    from sqlalchemy import func, select

    from app.models.invoice import Invoice
    from app.models.payment import Payment, PaymentRun

    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    async with mk() as db:
        ent = await _default_entity_id(db, info.org_id)
        inv = await _seed_invoice(
            db,
            info.org_id,
            ent,
            number="SF-BOUNDARY",
            amount="5000.00",
            due_date=date.today() + timedelta(days=5),
        )
        await db.commit()
        invoice_id = inv.id
        payments_before = (await db.execute(select(func.count(Payment.id)))).scalar_one()
        runs_before = (await db.execute(select(func.count(PaymentRun.id)))).scalar_one()

    await cash_flow_alerts._project_tenant(
        db_name=info.db_name,
        org_settings={"cashflow": {"min_balance_threshold": "1000000.00"}},
        ref_today=date.today(),
    )

    async with mk() as db:
        assert (await db.execute(select(func.count(Payment.id)))).scalar_one() == payments_before
        assert (await db.execute(select(func.count(PaymentRun.id)))).scalar_one() == runs_before
        status = (
            await db.execute(select(Invoice.status).where(Invoice.id == invoice_id))
        ).scalar_one()
        assert status == "approved"
