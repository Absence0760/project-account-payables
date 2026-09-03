"""Dynamic-discounting auto-capture sweep — auto-accept worthwhile open offers.

Every tick:

  1. Enumerate tenant DBs from the control plane.
  2. For each tenant, find ``offered`` :class:`~app.models.discount.DiscountOffer`
     rows that are still valid (not past ``valid_until``).
  3. For each, pick the best tier (highest discount percent) and compute its
     annualized ROI via :func:`app.services.discount_roi.compute_roi`, using the
     org's cost of capital
     (``Organization.settings.discounting.cost_of_capital_pct`` →
     ``settings.discount_cost_of_capital_pct`` fallback).
  4. If the annualized return clears ``settings.discount_auto_capture_roi_threshold``
     **and** the ROI is ``worthwhile``, AUTO-ACCEPT the offer: stamp
     ``accepted_tier`` / ``accepted_at`` and move ``status`` → ``accepted``, then
     write an append-only ``discount_offer.auto_accepted`` audit row.

Money-path boundary (important)
-------------------------------
This sweep ONLY accepts an offer — it flags it for capture by transitioning
``offered`` → ``accepted``. It deliberately does **not** execute a payment or
create any ``Payment`` / ``PaymentRun`` row. Actually moving the money still
flows through the normal CFO-gated payment-run path, exactly like a manually
accepted offer. Keeping the auto-trigger payment-free is what prevents it from
silently moving money: the worst it can do is mark a high-ROI discount as
"accepted", which a human still has to fund.

Idempotency
-----------
Only ``offered`` offers are touched, and accepting one moves it out of
``offered``. So a re-run never double-accepts — the status guard is the dedupe
(the same role ``renewal_alert_sent_at`` plays in ``contract_renewal``).

Mirrors the ``contract_renewal`` pattern: long-lived asyncio task started in
``main.lifespan``, fresh per-tenant engine, one tenant's failure logged but
never halts the sweep. Disabled by default (``FEOH_DISCOUNT_OPTIMIZATION_ENABLED``).
See ``backend/docs/dynamic-discounting.md``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.discount import (
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_OFFERED,
    DiscountOffer,
)
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.payment import PaymentSchedule
from app.services.audit_dispatch import dispatch_audit
from app.services.discount_offers import (
    best_tier_for_date,
    expire_if_past,
    offer_reference_date,
)
from app.services.discount_roi import compute_roi, days_between
from app.services.sweep_health import SWEEP_DISCOUNT_AUTO_TRIGGER, run_sweep_loop
from app.utils.dates import utc_today

logger = logging.getLogger(__name__)

# resolver: organization_id -> annual cost of capital (%) as Decimal.
OrgSettingsResolver = Callable[[uuid.UUID], Awaitable[Decimal]]


@dataclass
class AutoTriggerResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    offers_captured: int = 0
    failures: int = 0


def _tier_deadline(offer: DiscountOffer, tier: dict, ref_today: date) -> date:
    """Latest date we can pay and still earn ``tier`` — the discount deadline.

    Measured from the offer's own reference date
    (``discount_offers.offer_reference_date`` — ``valid_from``, else the date
    the offer was created) plus ``tier.days``, capped at the offer's
    ``valid_until`` when set (you can never capture after the offer expires).
    Using ``ref_today`` (today) as the reference regardless of when the offer
    opened makes every tier's deadline a ROLLING "N days from now" — a tier
    that should have expired 15 days ago instead looks achievable forever.

    The earlier fix only reached ``valid_from``, and ``build_bulk_offer``
    doesn't set one, so every bulk negotiation kept the rolling deadline. The
    ``ref_today`` fallback now applies only to an offer carrying neither date —
    an unpersisted one being previewed, where "from today" is correct.
    """
    reference = offer_reference_date(offer) or ref_today
    deadline = reference + timedelta(days=int(tier["days"]))
    if offer.valid_until is not None and deadline > offer.valid_until:
        return offer.valid_until
    return deadline


async def _resolve_due_date(db: AsyncSession, offer: DiscountOffer) -> date | None:
    """Net due date for an invoice-scoped offer — ``PaymentSchedule.due_date``
    (the authoritative payment due date) falling back to ``Invoice.due_date``.

    Returns ``None`` for vendor-scoped (bulk) offers or invoice-less offers; the
    caller then falls back to ``valid_until`` as the horizon (documented
    approximation — a bulk offer spans many invoices with no single due date).
    """
    if offer.invoice_id is None:
        return None
    due = (
        await db.execute(
            select(PaymentSchedule.due_date)
            .where(PaymentSchedule.invoice_id == offer.invoice_id)
            .order_by(PaymentSchedule.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if due is not None:
        return due
    return (
        await db.execute(select(Invoice.due_date).where(Invoice.id == offer.invoice_id))
    ).scalar_one_or_none()


async def _resolve_cost_of_capital(organization_id: uuid.UUID) -> Decimal:
    """Org's annual cost of capital (%): per-org override else platform default.

    Reads ``Organization.settings.discounting.cost_of_capital_pct`` from the
    control plane, falling back to ``settings.discount_cost_of_capital_pct``.
    """
    default = Decimal(str(settings.discount_cost_of_capital_pct))
    async with control_session_factory() as ctrl:
        raw = (
            await ctrl.execute(
                select(Organization.settings).where(Organization.id == organization_id)
            )
        ).scalar_one_or_none()
    if isinstance(raw, dict):
        discounting = raw.get("discounting")
        if isinstance(discounting, dict) and discounting.get("cost_of_capital_pct") is not None:
            try:
                return Decimal(str(discounting["cost_of_capital_pct"]))
            except (TypeError, ValueError, ArithmeticError):
                pass
    return default


async def run_auto_trigger_once(*, today: date | None = None) -> AutoTriggerResult:
    """One auto-capture sweep across every tenant. Safe to call directly."""
    result = AutoTriggerResult()
    ref_today = today or utc_today()

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(select(Organization.id, Organization.db_name))
        tenants = list(rows.all())

    for _org_id, db_name in tenants:
        result.tenants_scanned += 1
        try:
            result.offers_captured += await _sweep_tenant(
                db_name, ref_today, _resolve_cost_of_capital
            )
        except Exception as exc:  # noqa: BLE001 — one tenant must not halt the sweep
            logger.warning(
                "[discount-auto-trigger] failed sweeping %s: %s", db_name, exc.__class__.__name__
            )
            result.failures += 1

    if result.offers_captured or result.failures:
        logger.info(
            "[discount-auto-trigger] swept %d tenant(s); accepted=%d failed_sweeps=%d",
            result.tenants_scanned,
            result.offers_captured,
            result.failures,
        )
    return result


async def _sweep_tenant(
    db_name: str,
    ref_today: date,
    org_settings_resolver: OrgSettingsResolver,
) -> int:
    """Auto-accept worthwhile open offers for one tenant. Returns the count.

    Does NOT move money — only transitions ``offered`` → ``accepted`` (see the
    module docstring's money-path boundary).
    """
    threshold = Decimal(str(settings.discount_auto_capture_roi_threshold))
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            candidates = (
                (
                    await db.execute(
                        select(DiscountOffer).where(DiscountOffer.status == OFFER_STATUS_OFFERED)
                    )
                )
                .scalars()
                .all()
            )
            # NOT pre-filtered to valid_until >= ref_today: an `offered` row
            # past its own valid_until must still surface here so the
            # expire_if_past check below can actually flip it — previously
            # excluded entirely, which is exactly why an offer never
            # auto-expired anywhere (issue #124).

            # Resolve cost of capital once per distinct org (a tenant DB is one
            # org today, but the resolver contract is per-org).
            coc_cache: dict[uuid.UUID, Decimal] = {}
            accepted = 0
            for offer in candidates:
                # An offer whose valid_until has passed never auto-expired
                # anywhere else — expire_if_past was never invoked by any
                # sweep. Piggyback on this one (already running periodically)
                # rather than standing up a separate background loop.
                if expire_if_past(offer, as_of=ref_today):
                    continue

                # Date-window-enforced pick — the SAME rule the acceptance
                # endpoints use, measured from when the offer was actually
                # extended (`offer_reference_date`: `valid_from`, else the
                # offer's creation date), not from today. Without this, every
                # tier's deadline looked like "N days from now" and the sweep
                # always auto-accepted the highest-percent tier regardless of
                # how long the offer had been open.
                tier = best_tier_for_date(
                    offer.tiers or [],
                    ref_today,
                    offer.valid_until,
                    reference_date=offer_reference_date(offer),
                )
                if tier is None:
                    continue

                if offer.organization_id not in coc_cache:
                    coc_cache[offer.organization_id] = await org_settings_resolver(
                        offer.organization_id
                    )
                cost_of_capital = coc_cache[offer.organization_id]

                # Acceleration is the textbook horizon: pay on the discount
                # deadline instead of at the invoice's net due date
                # (days_between(pay_by, due_date)), NOT the discount period.
                pay_by = _tier_deadline(offer, tier, ref_today)
                due_date = await _resolve_due_date(db, offer) or offer.valid_until or pay_by
                roi = compute_roi(
                    base_amount=offer.base_amount,
                    discount_percent=Decimal(str(tier["percent"])),
                    days_accelerated=days_between(pay_by, due_date),
                    cost_of_capital_pct=cost_of_capital,
                )

                if not (roi.worthwhile and roi.annualized_return_pct >= threshold):
                    continue

                # Auto-accept: flag for capture. Money still moves via the
                # CFO-gated payment run — never here.
                offer.accepted_tier = tier
                offer.accepted_at = datetime.now(UTC)
                offer.status = OFFER_STATUS_ACCEPTED

                await dispatch_audit(
                    db,
                    correlation_id=uuid.uuid4(),
                    organization_id=offer.organization_id,
                    actor_id=None,  # system actor
                    action="discount_offer.auto_accepted",
                    entity_type="discount_offer",
                    entity_id=offer.id,
                    details={
                        "tier": tier,
                        "threshold_pct": str(threshold),
                        "roi": roi.as_dict(),  # Decimal-strings, no PII
                    },
                )
                accepted += 1

            await db.commit()
            return accepted
    finally:
        await engine.dispose()


async def run_discount_optimization_loop() -> None:
    """Long-lived loop started in ``main.lifespan``; cancelled on shutdown.
    Body is the shared ``sweep_health.run_sweep_loop``."""
    await run_sweep_loop(
        SWEEP_DISCOUNT_AUTO_TRIGGER,
        lambda: run_auto_trigger_once(),
        interval_seconds=settings.discount_optimization_interval_seconds,
        log=logger,
        log_prefix="[discount-auto-trigger]",
    )
