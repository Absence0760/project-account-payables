"""Periodic vendor re-screening sweep — background loop.

NOTE: foundation stub. The full per-tenant sweep is implemented by the
"ongoing monitoring" worker. Public symbols consumed by `main.lifespan`
(`run_vendor_rescreen_loop`) and tests (`rescreen_vendors_once`,
`RescreenResult`) are fixed — keep them stable.

Design (model after `services/contract_renewal.py`):

  1. Enumerate tenant DBs from the control plane.
  2. For each tenant, select active vendors whose `last_screened_at` is
     NULL or older than `settings.vendor_rescreen_after_days`.
  3. Re-screen each via `vendor_screening.screen_vendor_record`
     (`check_type="periodic"`); the trail row + denormalised state +
     payment block are handled there.
  4. Notify AP managers when a vendor newly flips to match / review.

Disabled by default (`AP_VENDOR_RESCREEN_ENABLED`); fresh per-tenant
engine; one tenant's failure is logged but never halts the sweep.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RescreenResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    vendors_screened: int = 0
    new_flags: int = 0
    failures: int = 0


async def rescreen_vendors_once() -> RescreenResult:
    """One sweep across every tenant. Safe to call directly (CLI / tests).

    Foundation stub — returns an empty result. Implemented by the
    ongoing-monitoring worker.
    """
    return RescreenResult()


async def run_vendor_rescreen_loop() -> None:
    """Long-lived loop started in `main.lifespan`; cancelled on shutdown."""
    interval = settings.vendor_rescreen_interval_seconds
    logger.info("[vendor-rescreen] started; interval=%ds", interval)
    try:
        while True:
            try:
                await rescreen_vendors_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("[vendor-rescreen] sweep raised: %s", exc, exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[vendor-rescreen] shutting down")
        raise
