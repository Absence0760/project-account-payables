"""Periodic vendor re-screening sweep — background loop ("ongoing monitoring").

Sanctions lists move. A vendor that screened ``clear`` at onboarding can land
on a watchlist months later, so a one-time screen is not enough — the roadmap
"ongoing monitoring" item re-screens active vendors on a cadence and re-blocks
any that newly hit. Every tick:

  1. Enumerate tenant DBs from the control plane (one query, also pulls each
     org's ``settings`` so we have the adapter config per tenant).
  2. For each tenant, open a fresh per-tenant engine and select ``active``
     vendors whose ``last_screened_at`` is NULL or older than the staleness
     window (``settings.vendor_rescreen_after_days``).
  3. Re-screen each via ``vendor_screening.screen_vendor_record``
     (``check_type="periodic"``) — the trail row, denormalised state, and the
     payment block are all handled there.
  4. Track how many vendors NEWLY flip to ``match`` / ``review`` (a vendor that
     was previously neither) and log an INFO line per new flag so the AP team
     can monitor. (No notification event-type exists for vendor screening and
     inventing one would need a migration / event-registry change; the re-screen
     itself — including the payment block on a ``match`` — is the load-bearing
     behaviour, so we deliberately fall back to logging here.)
  5. Commit once per tenant.

Mirrors ``services/contract_renewal.py``: long-lived asyncio task started in
``main.lifespan``, fresh per-tenant engine, one tenant's failure logged but
never halting the sweep. Disabled by default (``FEOH_VENDOR_RESCREEN_ENABLED``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.organization import Organization
from app.models.vendor import Vendor
from app.services.vendor_screening import screen_vendor_record

logger = logging.getLogger(__name__)

# Denormalised statuses that count as "flagged" — a vendor flipping INTO one of
# these (from anything else) is a new flag worth surfacing.
_FLAGGED_STATUSES = frozenset({"match", "review"})


@dataclass
class RescreenResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    vendors_screened: int = 0
    new_flags: int = 0
    failures: int = 0


async def rescreen_vendors_once(*, now: datetime | None = None) -> RescreenResult:
    """One sweep across every tenant. Safe to call directly (CLI / tests).

    ``now`` is injectable so the staleness cutoff is deterministic in tests;
    production leaves it None and it defaults to ``datetime.now(UTC)``.
    """
    result = RescreenResult()
    ref_now = now or datetime.now(UTC)
    cutoff = ref_now - timedelta(days=settings.vendor_rescreen_after_days)

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(
            select(Organization.id, Organization.db_name, Organization.settings)
        )
        tenants = list(rows.all())

    for _org_id, db_name, org_settings in tenants:
        result.tenants_scanned += 1
        try:
            screened, flags = await _sweep_tenant(db_name, org_settings, cutoff)
            result.vendors_screened += screened
            result.new_flags += flags
        except Exception as exc:  # noqa: BLE001 — one tenant must not halt the sweep
            # Log the exception CLASS only — a DB/asyncpg or sanctions-adapter
            # error message can echo a vendor name / partial banking value
            # (PII-out-of-logs).
            logger.warning(
                "[vendor-rescreen] failed sweeping %s: %s", db_name, exc.__class__.__name__
            )
            result.failures += 1

    if result.vendors_screened or result.new_flags or result.failures:
        logger.info(
            "[vendor-rescreen] swept %d tenant(s); screened=%d new_flags=%d failed_sweeps=%d",
            result.tenants_scanned,
            result.vendors_screened,
            result.new_flags,
            result.failures,
        )
    return result


async def _sweep_tenant(
    db_name: str, org_settings: dict | None, cutoff: datetime
) -> tuple[int, int]:
    """Re-screen due vendors for one tenant.

    Returns ``(vendors_screened, new_flags)``. Commits once on success.
    """
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            due = (
                (
                    await db.execute(
                        select(Vendor).where(
                            Vendor.status == "active",
                            or_(
                                Vendor.last_screened_at.is_(None),
                                Vendor.last_screened_at < cutoff,
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )

            screened = 0
            new_flags = 0
            for vendor in due:
                # Capture the pre-screen status so we only count a vendor that
                # was NOT already flagged flipping into match / review.
                was_flagged = vendor.screening_status in _FLAGGED_STATUSES

                outcome = await screen_vendor_record(
                    db,
                    vendor=vendor,
                    organization_id=vendor.organization_id,
                    org_settings=org_settings,
                    check_type="periodic",
                )
                screened += 1

                if not was_flagged and outcome.screening_status in _FLAGGED_STATUSES:
                    new_flags += 1
                    # No notification event-type exists for vendor screening
                    # (see module docstring); log so the AP team can monitor.
                    # PII-free: vendor id + verdict + list NAME only.
                    logger.info(
                        "[vendor-rescreen] vendor=%s newly flagged status=%s "
                        "matched_list=%s tenant=%s",
                        vendor.id,
                        outcome.screening_status,
                        outcome.matched_list,
                        db_name,
                    )

            await db.commit()
            return screened, new_flags
    finally:
        await engine.dispose()


async def run_vendor_rescreen_loop() -> None:
    """Long-lived loop started in `main.lifespan`; cancelled on shutdown."""
    interval = settings.vendor_rescreen_interval_seconds
    logger.info("[vendor-rescreen] started; interval=%ds", interval)
    try:
        while True:
            try:
                await rescreen_vendors_once()
            except Exception as exc:  # noqa: BLE001
                # Class name in the message; exc_info=True keeps the traceback
                # for debugging without putting the exception text (possible PII)
                # in the log format string.
                logger.error(
                    "[vendor-rescreen] sweep raised: %s", exc.__class__.__name__, exc_info=True
                )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[vendor-rescreen] shutting down")
        raise
