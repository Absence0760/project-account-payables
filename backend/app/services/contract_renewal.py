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
never halts the sweep. Within a tenant it mirrors ``vendor_rescreen``: the two
passes are independent, and each contract is locked, re-checked and committed
**on its own** inside a ``try`` / ``rollback`` guard — so one bad contract can
neither abort the tick nor roll back the other pass's work (see
:func:`_sweep_tenant` and ``backend/docs/background-sweeps.md`` § Locking).
Disabled by default (``FEOH_CONTRACT_RENEWAL_ENABLED``).
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
from app.utils.dates import utc_today

logger = logging.getLogger(__name__)


@dataclass
class RenewalResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    alerts_sent: int = 0
    contracts_expired: int = 0
    #: Tenants whose sweep aborted outright (engine/connect/candidate-query
    #: failure).
    failures: int = 0
    #: Individual contracts whose alert or expiry raised — in EITHER pass.
    #: Counted apart from ``failures`` because one bad contract no longer takes
    #: its tenant's remaining contracts down with it, and no longer unwinds the
    #: other pass. Mirrors ``vendor_rescreen``'s ``vendor_failures``. The
    #: ``*_failures`` suffix is load-bearing: ``sweep_health.failure_count``
    #: sums exactly ``failures`` and any ``*_failures`` field, so a tick that
    #: keeps completing while every contract inside it raises reports
    #: ``partial`` (and past the streak, ``degraded``) instead of ``ok``.
    contract_failures: int = 0


@dataclass
class TenantSweepOutcome:
    """One tenant's outcome — both passes plus their shared failure counter."""

    alerts_sent: int = 0
    contracts_expired: int = 0
    contract_failures: int = 0


async def notify_renewals_once(*, today: date | None = None) -> RenewalResult:
    """One sweep across every tenant. Safe to call directly (CLI / tests)."""
    result = RenewalResult()
    ref_today = today or utc_today()

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(select(Organization.id, Organization.db_name))
        tenants = list(rows.all())

    for _org_id, db_name in tenants:
        result.tenants_scanned += 1
        try:
            outcome = await _sweep_tenant(db_name, ref_today)
            result.alerts_sent += outcome.alerts_sent
            result.contracts_expired += outcome.contracts_expired
            result.contract_failures += outcome.contract_failures
        except Exception as exc:  # noqa: BLE001 — one tenant must not halt the sweep
            # Log the class, not the message (PII-out-of-logs invariant).
            logger.warning(
                "[contract-renewal] failed sweeping %s: %s", db_name, exc.__class__.__name__
            )
            result.failures += 1

    if (
        result.alerts_sent
        or result.contracts_expired
        or result.failures
        or result.contract_failures
    ):
        logger.info(
            "[contract-renewal] swept %d tenant(s); alerts=%d expired=%d failed_sweeps=%d "
            "failed_contracts=%d",
            result.tenants_scanned,
            result.alerts_sent,
            result.contracts_expired,
            result.failures,
            result.contract_failures,
        )
    return result


async def _sweep_tenant(db_name: str, ref_today: date) -> TenantSweepOutcome:
    """Notify due renewals + expire over-term contracts for one tenant.

    Two independent passes over the same session, each **committing per
    contract** inside its own ``try`` / ``rollback`` guard.

    Both passes used to share one transaction and one commit at the end of the
    tick, which coupled three things that have nothing to do with each other:

    1. One contract whose alert raised (a recipient lookup failing, a
       notification bug, an audit write that will not land) discarded every
       ``renewal_alert_sent_at`` stamp already made on that tick — so the
       recipients who HAD been emailed got the same alert again next tick,
       while the poison contract, being deterministic, aborted at exactly the
       same place forever and nothing after it was ever alerted.
    2. A raise in the **expiry** pass rolled back the whole **alert** pass, and
       vice versa — two unrelated controls, each able to silently undo the
       other's work.
    3. That tenant then made zero forward progress on either control, with the
       only trace a per-tenant counter the loop discarded.

    Candidate ids are selected unlocked and ordered by id (one lock order for
    every replica, so concurrent sweeps queue instead of deadlocking); each is
    re-read with ``FOR UPDATE`` and **re-checked against the predicate the id
    query used** — it can have changed under us (a renew, a terminate, a
    manual expiry) between the two statements. A leg with nothing to write ends
    in ``rollback()``, releasing the row lock immediately rather than at the end
    of the tick. Same shape as ``vendor_rescreen`` / ``recurring_invoices`` /
    ``approval_escalation``; see ``backend/docs/background-sweeps.md`` § Locking.
    """
    # Coarse pre-filter by the platform-max lead window, then refine per
    # contract by its own renewal_notice_days. Keeps the fetched set small
    # without baking a per-row interval into SQL.
    horizon = ref_today + timedelta(days=3650)
    outcome = TenantSweepOutcome()
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            alert_ids = (
                (
                    await db.execute(
                        select(Contract.id)
                        .where(
                            Contract.status == ContractStatus.active,
                            Contract.end_date.is_not(None),
                            Contract.end_date <= horizon,
                            Contract.renewal_alert_sent_at.is_(None),
                        )
                        .order_by(Contract.id.asc())
                    )
                )
                .scalars()
                .all()
            )

            for contract_id in alert_ids:
                try:
                    # `with_for_update` bypasses the identity map, so this is a
                    # real `SELECT ... FOR UPDATE` on exactly one row.
                    contract = await db.get(Contract, contract_id, with_for_update=True)
                    if (
                        contract is None
                        or contract.status != ContractStatus.active
                        or contract.end_date is None
                        or contract.renewal_alert_sent_at is not None
                    ):
                        # Deleted, renewed, terminated or alerted elsewhere
                        # between the id read and the lock.
                        await db.rollback()
                        continue

                    days_until = (contract.end_date - ref_today).days
                    if days_until > contract.renewal_notice_days:
                        # Not yet within this contract's lead window.
                        await db.rollback()
                        continue

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
                        await db.rollback()
                        continue

                    rendered = render_contract_renewal(
                        contract_number=contract.contract_number,
                        vendor_name=vendor_name,
                        end_date=contract.end_date,
                        days_until=days_until,
                    )
                    notified = await notify_event(
                        db,
                        correlation_id=uuid.uuid4(),
                        organization_id=contract.organization_id,
                        event_type=EVENT_CONTRACT_RENEWAL_DUE,
                        entity_id=contract.id,
                        recipient_user_ids=recipients,
                        rendered=rendered,
                        entity_type="contract",
                    )
                    if not notified:
                        # Same reasoning as the recipient-less branch above, one
                        # layer deeper. `notify_event` never raises, so an off
                        # master switch / a failed recipient load / everyone having
                        # the event opted out all looked exactly like a delivered
                        # alert. Stamping `renewal_alert_sent_at` on that swallows
                        # the warning for the whole remaining term (only
                        # `POST /api/contracts/{id}/renew` ever clears it) while
                        # `alerts_sent` counts it as delivered.
                        logger.warning(
                            "[contract-renewal] contract=%s: renewal alert reached no "
                            "recipient; leaving renewal_alert_sent_at unset so the next "
                            "tick retries",
                            contract.id,
                        )
                        await db.rollback()
                        continue
                    contract.renewal_alert_sent_at = datetime.now(UTC)
                    await db.commit()
                    outcome.alerts_sent += 1
                except Exception as exc:  # noqa: BLE001 — one contract must not halt the tenant
                    # Class only — a DB/asyncpg or notification error message can
                    # echo a vendor name or a contract value (PII-out-of-logs).
                    logger.warning(
                        "[contract-renewal] contract=%s renewal alert failed in %s: %s",
                        contract_id,
                        db_name,
                        exc.__class__.__name__,
                    )
                    await db.rollback()
                    outcome.contract_failures += 1
                    continue

            # End-of-term expiry: an `active` contract whose end_date has
            # actually passed (not just approaching) moves to `expired`. This
            # is the only runtime path that ever sets `ContractStatus.expired`
            # — see the module docstring. Re-querying `active` here (rather
            # than reusing the alert candidates) keeps this pass correct even
            # though the two conditions overlap; the status guard means a
            # contract already flipped to `expired` never matches again, so a
            # repeat sweep is a no-op (idempotent, no double audit row). This
            # pass is INDEPENDENT of the one above: it runs on its own
            # per-contract transactions, so neither can roll the other back.
            overdue_ids = (
                (
                    await db.execute(
                        select(Contract.id)
                        .where(
                            Contract.status == ContractStatus.active,
                            Contract.end_date.is_not(None),
                            Contract.end_date < ref_today,
                        )
                        .order_by(Contract.id.asc())
                    )
                )
                .scalars()
                .all()
            )

            for contract_id in overdue_ids:
                try:
                    contract = await db.get(Contract, contract_id, with_for_update=True)
                    if (
                        contract is None
                        or contract.status != ContractStatus.active
                        or contract.end_date is None
                        or contract.end_date >= ref_today
                    ):
                        # Deleted, renewed or transitioned between the id read
                        # and the lock — re-checking the predicate under the
                        # lock is what stops a stale snapshot expiring a
                        # contract someone just renewed.
                        await db.rollback()
                        continue
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
                    await db.commit()
                    outcome.contracts_expired += 1
                except Exception as exc:  # noqa: BLE001 — one contract must not halt the tenant
                    logger.warning(
                        "[contract-renewal] contract=%s expiry failed in %s: %s",
                        contract_id,
                        db_name,
                        exc.__class__.__name__,
                    )
                    await db.rollback()
                    outcome.contract_failures += 1
                    continue

            return outcome
    finally:
        await engine.dispose()


async def run_renewal_loop() -> None:
    """Long-lived loop started in ``main.lifespan``; cancelled on shutdown.
    Body is the shared ``sweep_health.run_sweep_loop`` — each tick's
    ``RenewalResult`` is recorded there, and BOTH its counters feed the health
    streak: ``failures`` (a whole tenant's sweep aborted) and
    ``contract_failures`` (individual alerts/expiries that raised)."""
    await run_sweep_loop(
        SWEEP_CONTRACT_RENEWAL,
        lambda: notify_renewals_once(),
        interval_seconds=settings.contract_renewal_interval_seconds,
        log=logger,
        log_prefix="[contract-renewal]",
    )
