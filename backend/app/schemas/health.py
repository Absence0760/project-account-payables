"""Schemas for the health surface.

Deliberately narrow. ``SweepHealthOut`` is the contract for
``GET /api/health/sweeps`` and carries **no cross-tenant cardinality**: the raw
per-sweep counters (``tenants_scanned``, ``rows_shipped``, …) stay in the
in-process registry and the logs, because an ordinary tenant admin holds
``ROLE_ADMIN`` and would otherwise learn how many organizations the platform
sweeps. Only ``last_failure_count`` — the actionable number, and the one that is
zero on a healthy platform — crosses the boundary, alongside the state,
timestamps and the exception CLASS.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SweepHealthOut(BaseModel):
    """One background sweep's health, PII-free by construction."""

    name: str
    #: not_started | disabled | starting | running | idle | stopped | died
    state: str
    #: Whether this sweep's FEOH_*_ENABLED flag is on in this process.
    enabled: bool
    started_at: datetime | None = None
    last_run_started_at: datetime | None = None
    last_run_finished_at: datetime | None = None
    #: ok | partial | error — `partial` means the tick completed but the sweep
    #: reported failures of its own.
    last_outcome: str | None = None
    #: Exception CLASS name only. Never `str(exc)`.
    last_error_class: str | None = None
    #: Failures the most recent tick reported (0 on a clean run).
    last_failure_count: int = 0
    consecutive_failures: int = 0
    total_runs: int = 0
    total_failed_runs: int = 0
    #: Set when the task ended on its own rather than being cancelled.
    exit_error_class: str | None = None


class SweepHealthReport(BaseModel):
    """Aggregate + per-sweep report for this process (not the cluster)."""

    #: ok | degraded | failing — worst sweep wins.
    state: str
    #: Consecutive failed runs at which a sweep is called degraded
    #: (FEOH_SWEEP_FAILURE_ALERT_STREAK); 0 disables the escalation.
    failure_alert_streak: int
    sweeps: list[SweepHealthOut]
