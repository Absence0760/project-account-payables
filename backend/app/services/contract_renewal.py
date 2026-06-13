"""Contract renewal alerts — background sweep that notifies before expiry.

Every tick:

  1. Enumerate tenant DBs from the control plane.
  2. For each tenant, find ``active`` contracts with an ``end_date``, no
     renewal alert sent yet, that fall within their own
     ``renewal_notice_days`` lead window (already-expired-but-unalerted
     contracts qualify too).
  3. Notify the contract owner + every AP manager, once, via
     ``notification_dispatch.notify_event`` (in-app + email, preference-gated).
  4. Stamp ``renewal_alert_sent_at = now()`` so the alert never re-fires for
     this term. ``POST /api/contracts/{id}/renew`` clears it, re-arming the
     alert for the new end date.

Mirrors the ``audit_log_shipper`` pattern: long-lived asyncio task started in
``main.lifespan``, fresh per-tenant engine, one tenant's failure logged but
never halts the sweep. Disabled by default (``AP_CONTRACT_RENEWAL_ENABLED``).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.contract import Contract, ContractStatus
from app.models.notification import EVENT_CONTRACT_RENEWAL_DUE
from app.models.organization import Organization
from app.models.vendor import Vendor
from app.services.notification_dispatch import notify_event, resolve_role_user_ids
from app.services.notification_templates import render_contract_renewal

logger = logging.getLogger(__name__)


@dataclass
class RenewalResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    alerts_sent: int = 0
    failures: int = 0


async def notify_renewals_once(*, today: date | None = None) -> RenewalResult:
    """One sweep across every tenant. Safe to call directly (CLI / tests)."""
    result = RenewalResult()
    ref_today = today or datetime.now(UTC).date()

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(select(Organization.id, Organization.db_name))
        tenants = list(rows.all())

    for _org_id, db_name in tenants:
        result.tenants_scanned += 1
        try:
            result.alerts_sent += await _sweep_tenant(db_name, ref_today)
        except Exception as exc:  # noqa: BLE001 — one tenant must not halt the sweep
            logger.warning("[contract-renewal] failed sweeping %s: %s", db_name, exc)
            result.failures += 1

    if result.alerts_sent or result.failures:
        logger.info(
            "[contract-renewal] swept %d tenant(s); alerts=%d failed_sweeps=%d",
            result.tenants_scanned,
            result.alerts_sent,
            result.failures,
        )
    return result


async def _sweep_tenant(db_name: str, ref_today: date) -> int:
    """Notify due renewals for one tenant. Returns the count of alerts sent."""
    # Coarse pre-filter by the platform-max lead window, then refine per
    # contract by its own renewal_notice_days. Keeps the fetched set small
    # without baking a per-row interval into SQL.
    horizon = ref_today + timedelta(days=3650)
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            candidates = (
                (
                    await db.execute(
                        select(Contract).where(
                            Contract.status == ContractStatus.active,
                            Contract.end_date.is_not(None),
                            Contract.end_date <= horizon,
                            Contract.renewal_alert_sent_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )

            sent = 0
            for contract in candidates:
                days_until = (contract.end_date - ref_today).days
                if days_until > contract.renewal_notice_days:
                    continue  # not yet within this contract's lead window

                vendor_name = (
                    await db.execute(select(Vendor.name).where(Vendor.id == contract.vendor_id))
                ).scalar_one_or_none()

                recipients = await resolve_role_user_ids(contract.organization_id, "ap_manager")
                if contract.owner_user_id:
                    recipients.append(contract.owner_user_id)
                if not recipients:
                    # No one to notify yet — leave renewal_alert_sent_at unset so
                    # a later sweep (once the org has an AP manager / owner) still
                    # fires the alert for this term. Re-scanning a recipient-less
                    # contract each tick is cheap; silently dropping the alert is
                    # not.
                    continue

                rendered = render_contract_renewal(
                    contract_number=contract.contract_number,
                    vendor_name=vendor_name,
                    end_date=contract.end_date,
                    days_until=days_until,
                )
                await notify_event(
                    db,
                    correlation_id=uuid.uuid4(),
                    organization_id=contract.organization_id,
                    event_type=EVENT_CONTRACT_RENEWAL_DUE,
                    entity_id=contract.id,
                    recipient_user_ids=recipients,
                    rendered=rendered,
                    entity_type="contract",
                )
                contract.renewal_alert_sent_at = datetime.now(UTC)
                sent += 1

            await db.commit()
            return sent
    finally:
        await engine.dispose()


async def run_renewal_loop() -> None:
    """Long-lived loop started in ``main.lifespan``; cancelled on shutdown."""
    interval = settings.contract_renewal_interval_seconds
    logger.info("[contract-renewal] started; interval=%ds", interval)
    try:
        while True:
            try:
                await notify_renewals_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("[contract-renewal] sweep raised: %s", exc, exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[contract-renewal] shutting down")
        raise
