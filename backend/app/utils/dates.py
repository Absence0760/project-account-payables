"""One definition of "today" for the backend.

``date.today()`` resolves in the **server's local timezone**. On the deployed
shape (a UTC container) that agrees with UTC, so a mixture is latent rather than
live — which is exactly what makes it easy to keep introducing.

It stops being latent the moment anything runs off a non-UTC host, because the
cash-flow stack derives more than a display value from "today":

* ``api/analytics._commitment_rows`` bounds its horizon on it, so the `/cfo`
  chart and a copilot answer built minutes apart can be assembled from
  *different row sets* for part of each day;
* ``services/cash_flow_plan.compute_plan_id`` hashes it, so a plan proposed near
  midnight can 409 as stale against its own enact call;
* ``services/scheduled_reports`` slices each report's ``period_days`` window
  from it, so an emailed snapshot and the API export of the same report
  disagree at the boundary.

``app/api/cash_flow.py`` and every copilot tool already resolve it as
``datetime.now(UTC).date()``. This module is that expression with a name, so the
rest of the stack can converge on it instead of each site re-deciding.

Guard: ``tests/test_utc_today.py`` AST-scans the modules that have converged and
fails if a bare ``date.today()`` / ``datetime.today()`` reappears in one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

__all__ = ["utc_today"]


def utc_today() -> date:
    """Today's date in UTC — never the server's local timezone."""
    return datetime.now(UTC).date()
