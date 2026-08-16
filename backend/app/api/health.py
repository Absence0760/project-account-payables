"""Health surface — the public liveness probe plus the operator sweep report.

``GET /api/health`` is unchanged and stays **public-by-design**: it is the load
balancer's liveness probe, it takes no auth, and it must keep returning a static
``{"status": "ok"}``. It deliberately does NOT fold in sweep health — a degraded
background sweep is not a reason to pull a healthy process out of rotation, and
wiring it in would turn "the audit shipper's sink is misconfigured" into a
rolling restart loop.

``GET /api/health/sweeps`` is the operator view the follow-up asked for: which
long-lived sweeps are alive, when each last ran, and whether any is failing.
Admin-gated, PII-free, and free of cross-tenant cardinality (see
``app/schemas/health.py``). Process-local by design — with several replicas,
each answers for itself, which is precisely the question an operator asks of a
suspect process. See ``../../docs/decisions.md`` §24.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import ROLE_ADMIN, require_roles
from app.models.user import User
from app.schemas.health import SweepHealthOut, SweepHealthReport
from app.services import sweep_health

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Liveness probe. Public-by-design, static body — do not extend it."""
    return {"status": "ok"}


@router.get("/health/sweeps", response_model=SweepHealthReport)
async def sweep_report(
    _user: User = Depends(require_roles(ROLE_ADMIN)),
) -> SweepHealthReport:
    """Per-sweep health for THIS process.

    Every long-lived sweep reports each tick's outcome into
    ``services/sweep_health``; this reads that registry. A sweep whose
    ``FEOH_*_ENABLED`` flag is on but which never registered comes back
    ``not_started`` and flips the aggregate to ``failing`` — that is the
    "supposed to be running and isn't" case, which used to be invisible.
    """
    rows = sweep_health.snapshot()
    return SweepHealthReport(
        state=sweep_health.overall_state(rows),
        failure_alert_streak=sweep_health.alert_streak(),
        sweeps=[
            SweepHealthOut(
                name=row.name,
                state=row.state,
                enabled=row.enabled,
                started_at=row.started_at,
                last_run_started_at=row.last_run_started_at,
                last_run_finished_at=row.last_run_finished_at,
                last_outcome=row.last_outcome,
                last_error_class=row.last_error_class,
                last_failure_count=sweep_health.failure_count(row.last_counts or {}),
                consecutive_failures=row.consecutive_failures,
                total_runs=row.total_runs,
                total_failed_runs=row.total_failed_runs,
                exit_error_class=row.exit_error_class,
            )
            for row in rows
        ],
    )
