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
  5. Separately, find ``active`` contracts whose ``end_date`` has actually
     PASSED (not just approaching) and transition them to ``expired``,
     writing a ``contract.expired`` audit row. This is the only runtime path
     that ever sets ``ContractStatus.expired`` — without it a contract past
     its term stays ``active`` forever, spend-to-contract / renewal reporting
     treats it as still live, and the ``expired -> ...`` transition branches
     in ``api/contracts.py`` (``activate``/``terminate``) can never fire.

Idempotency (expiry pass)
--------------------------
Only ``active`` contracts are matched, and expiring one moves it out of
``active``. So a re-run never double-expires or double-audits — the status
guard is the dedupe, exactly like the ``renewal_alert_sent_at`` marker above
and the ``offered`` status guard in ``discount_auto_trigger``.

Mirrors the ``audit_log_shipper`` pattern: long-lived asyncio task started in
``main.lifespan``, fresh per-tenant engine, one tenant's failure logged but
never halts the sweep. Disabled by default (``FEOH_CONTRACT_RENEWAL_ENABLED``).
"""

from __future__ import annotations

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
from app.services.audit_dispatch import dispatch_audit
from app.services.notification_dispatch import notify_event, resolve_role_user_ids
from app.services.notification_templates import render_contract_renewal
from app.services.sweep_health import SWEEP_CONTRACT_RENEWAL, run_sweep_loop

logger = logging.getLogger(__name__)


@dataclass
class RenewalResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    alerts_sent: int = 0
    contracts_expired: int = 0
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
            sent, expired = await _sweep_tenant(db_name, ref_today)
            result.alerts_sent += sent
            result.contracts_expired += expired
        except Exception as exc:  # noqa: BLE001 — one tenant must not halt the sweep
            # Log the class, not the message (PII-out-of-logs invariant).
            logger.warning(
                "[contract-renewal] failed sweeping %s: %s", db_name, exc.__class__.__name__
            )
            result.failures += 1

    if result.alerts_sent or result.contracts_expired or result.failures:
        logger.info(
            "[contract-renewal] swept %d tenant(s); alerts=%d expired=%d failed_sweeps=%d",
            result.tenants_scanned,
            result.alerts_sent,
            result.contracts_expired,
            result.failures,
        )
    return result


async def _sweep_tenant(db_name: str, ref_today: date) -> tuple[int, int]:
    """Notify due renewals + expire over-term contracts for one tenant.

    Returns ``(alerts_sent, contracts_expired)``.
    """
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

            # End-of-term expiry: an `active` contract whose end_date has
            # actually passed (not just approaching) moves to `expired`. This
            # is the only runtime path that ever sets `ContractStatus.expired`
            # — see the module docstring. Re-querying `active` here (rather
            # than reusing `candidates`) keeps this pass correct even though
            # the two conditions overlap; the status guard means a contract
            # already flipped to `expired` never matches again, so a repeat
            # sweep is a no-op (idempotent, no double audit row).
            overdue = (
                (
                    await db.execute(
                        select(Contract).where(
                            Contract.status == ContractStatus.active,
                            Contract.end_date.is_not(None),
                            Contract.end_date < ref_today,
                        )
                    )
                )
                .scalars()
                .all()
            )

            expired = 0
            for contract in overdue:
                contract.status = ContractStatus.expired
                await dispatch_audit(
                    db,
                    correlation_id=uuid.uuid4(),
                    organization_id=contract.organization_id,
                    actor_id=None,  # system actor
                    action="contract.expired",
                    entity_type="contract",
                    entity_id=contract.id,
                    details={"contract_number": contract.contract_number},
                )
                expired += 1

            await db.commit()
            return sent, expired
    finally:
        await engine.dispose()


async def run_renewal_loop() -> None:
    """Long-lived loop started in ``main.lifespan``; cancelled on shutdown.
    Body is the shared ``sweep_health.run_sweep_loop`` — each tick's
    ``RenewalResult`` (including its ``failures``) is recorded there."""
    await run_sweep_loop(
        SWEEP_CONTRACT_RENEWAL,
        lambda: notify_renewals_once(),
        interval_seconds=settings.contract_renewal_interval_seconds,
        log=logger,
        log_prefix="[contract-renewal]",
    )
