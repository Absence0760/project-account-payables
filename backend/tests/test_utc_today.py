"""The cash-flow stack resolves "today" in UTC, and stays that way.

`date.today()` uses the SERVER's local timezone. On a UTC container — the
deployed shape — that agrees with `datetime.now(UTC).date()`, so a mixture is
latent rather than live. That is exactly what makes it easy to keep
reintroducing, and it stops being latent the moment anything runs off a non-UTC
host, because these modules derive more than a display value from "today":

  * `api/analytics._commitment_rows` bounds its horizon on it, so the `/cfo`
    chart and a copilot answer built minutes apart can be assembled from
    different row sets for part of each day;
  * `services/cash_flow_plan.compute_plan_id` hashes it, so a plan proposed near
    midnight can 409 as stale against its own enact call;
  * `services/scheduled_reports` slices each report's `period_days` window from
    it, so the emailed snapshot and the API export of the same report disagree
    at the boundary.

`app/utils/dates.utc_today()` is the one definition. The scan below is scoped to
the modules that have **converged** on it — a deliberate allowlist, not the
whole tree: ~45 other `date.today()` call sites exist across the app (1099
forms, positive pay, recurring templates, the portal), and each is its own
conversion with its own reasoning. Widening this set is how they get converted;
what the guard prevents is a converted module quietly sliding back.

Shape borrowed from `tests/test_payment_methods.py`'s source scan.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, date, datetime

import pytest

from app.utils.dates import utc_today

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

#: Modules that resolve "today" in UTC and must keep doing so. Add to this list
#: when you convert a module; never remove from it.
UTC_TODAY_MODULES = (
    "api/analytics.py",
    "api/cash_flow.py",
    "services/scheduled_reports.py",
    "services/cash_flow_plan.py",
    "services/cash_flow_alerts.py",
    "services/assistant/tools/cashflow.py",
    "services/assistant/tools/forecast.py",
    "services/assistant/tools/optimizer.py",
    "services/assistant/tools/vendor_spend.py",
)


def test_utc_today_is_the_utc_calendar_date():
    assert utc_today() == datetime.now(UTC).date()
    assert isinstance(utc_today(), date)


@pytest.mark.parametrize("relative", UTC_TODAY_MODULES)
def test_module_never_reads_the_servers_local_today(relative):
    """Fails on any `date.today()` / `datetime.today()` in a converted module.

    If this fires, import `utc_today` from `app/utils/dates.py` (or inline
    `datetime.now(UTC).date()`) rather than reaching for the local-timezone
    call — the two agree only while the host happens to run UTC.
    """
    path = APP_DIR / relative
    assert path.exists(), f"{relative} moved — update UTC_TODAY_MODULES"

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "today":
            continue
        # `date.today()` / `datetime.today()` — including aliases like
        # `_dt.today()`, which is how the local import in
        # `scheduled_reports._materialise_rows` spells it.
        if isinstance(func.value, ast.Name):
            offenders.append(node.lineno)

    assert not offenders, (
        f"{relative} reads the server's local 'today' at line(s) {offenders}. "
        "Use app.utils.dates.utc_today() — these modules feed the cash-flow "
        "horizon, the plan_id hash and the scheduled-report window, all of "
        "which must agree with each other on a non-UTC host."
    )
