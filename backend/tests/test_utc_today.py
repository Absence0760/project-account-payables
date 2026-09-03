"""The backend resolves "today" in UTC, and stays that way.

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
whole tree. Widening this set is how a module gets converted; what the guard
prevents is a converted module quietly sliding back.

The list is no longer only the cash-flow stack. The second wave took the AP
surfaces whose "today" is a comparison rather than a display value — the
discount deadline (`api/discounts`, `api/portal`, `services/analytics`), the
past-due and future-invoice-date fraud flags (`services/invoice_warnings`), the
recurring-template period key that IS the generation idempotency key
(`api/recurring`), and the regulated `Invoice.approval_date` written on all
three approval paths (`services/review`, `services/extraction`, `api/workflow`)
— plus the provenance stamps and export filenames that sit beside a
`datetime.now(UTC)` in the same response and disagreed with it for part of each
day on a non-UTC host.

Shape borrowed from `tests/test_payment_methods.py`'s source scan.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, date, datetime

import pytest

from app.utils.dates import utc_today

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
TESTS_DIR = pathlib.Path(__file__).resolve().parent

#: Modules that resolve "today" in UTC and must keep doing so. Add to this list
#: when you convert a module; never remove from it.
UTC_TODAY_MODULES = (
    # Wave 1 — the cash-flow stack.
    "api/analytics.py",
    "api/cash_flow.py",
    "services/scheduled_reports.py",
    "services/cash_flow_plan.py",
    "services/cash_flow_alerts.py",
    "services/assistant/tools/cashflow.py",
    "services/assistant/tools/forecast.py",
    "services/assistant/tools/optimizer.py",
    "services/assistant/tools/vendor_spend.py",
    # Wave 2 — the AP surfaces. Comparisons first: a date compared against a
    # discount deadline, a due date, a contract end date or a period key.
    "api/dashboard.py",
    "api/discounts.py",
    "api/payments.py",
    "api/portal.py",
    "api/recurring.py",
    "api/workflow.py",
    "services/analytics.py",
    "services/contract_compliance.py",
    "services/extraction.py",
    "services/invoice_warnings.py",
    "services/review.py",
    # …then the stamps and filenames. Lower stakes individually, but each sits
    # beside a `datetime.now(UTC)` in the same response, so a local-time answer
    # made one document disagree with itself.
    "api/audit.py",
    "api/expenses.py",
    "api/positive_pay.py",
    "api/reports.py",
    "api/tax.py",
    "services/expense_card_reconciliation.py",
    "services/extraction_adapters/mock_adapter.py",
    "services/positive_pay.py",
    "services/report_export.py",
    "services/tax_1099.py",
    "services/tax_1099_forms.py",
)

#: Test modules that anchor their fixtures on the UTC date and must keep doing
#: so. A test is not exempt from this: when the module under test resolves
#: "today" in UTC and the test builds its expectations from the LOCAL date, the
#: two disagree for the whole window each day where the local calendar date
#: differs from UTC's — several hours daily anywhere west of UTC, and the
#: entire working day in Asia-Pacific. CI runners are UTC, so the suite reads
#: green there no matter how wrong the test is; the failure only ever surfaces
#: on a contributor's laptop, as an unreproducible date assertion.
#:
#: Found by running the suite under `TZ=Pacific/Kiritimati` (UTC+14, so the
#: local date is a day AHEAD of UTC), which turns the whole class of bug from a
#: clock-watching flake into a deterministic failure:
#:
#:     TZ=Pacific/Kiritimati pytest -q
#:
#: That is the way to check a new date-sensitive test, and the way this list was
#: derived: 36 test modules read the local date, but only these five compared it
#: against an app-computed UTC value. The other 31 use it self-consistently
#: (fixture and assertion from the same sample), so they are deliberately NOT
#: listed — converting them would be churn, and this allowlist is opt-in by
#: design, exactly like the app-module list above.
UTC_TODAY_TEST_MODULES = (
    # `services/invoice_warnings` past-due + future-invoice-date fraud flags.
    "test_exception_flow.py",
    # `api/tax` / `services/tax_1099` W-9 received-date stamp.
    "test_tax.py",
    # `api/portal` W-9 self-service stamp — the same column, other surface.
    "test_portal_tax_forms.py",
    # `services/analytics` aging-band boundaries.
    "test_analytics_aging_reconciliation.py",
    # `api/dashboard` upcoming-payment overdue inequality.
    "test_dashboard_aggregations.py",
)


def local_today_call_lines(source: str, *, filename: str = "<test>") -> list[int]:
    """Line numbers of every local-timezone "today" call in ``source``.

    Matches every spelling that was live in this codebase:

    * ``date.today()`` / ``datetime.today()`` — and aliases like ``_dt.today()``,
      which is how the local import in ``scheduled_reports._materialise_rows``
      spells it (``func.value`` is a ``Name``);
    * ``datetime.date.today()`` — the module-style call ``api/positive_pay`` and
      ``services/positive_pay`` used (``func.value`` is an ``Attribute``). The
      original Name-only check missed this form entirely, so either module could
      have been added to the allowlist above while still reading local time.

    …plus a third spelling nothing in ``app/`` uses yet, and the one a purely
    ``.today()``-shaped guard could never see: ``datetime.now().date()``. A bare
    ``now()`` returns a NAIVE local-time datetime, so ``.date()`` on it is the
    server's local date wearing a name that reads as deliberate.
    ``datetime.now(UTC).date()`` is correct and passes — the tz argument is the
    entire difference, so the check is on the empty call, not the attribute.

    A comment or docstring mentioning ``date.today()`` is never flagged — the
    scan is over the AST, not the text, which is what lets a converted module
    keep explaining why it converted.
    """

    def _is_local_today(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            return False
        if node.func.attr == "today":
            # `date.today()` / `datetime.today()` / `_dt.today()` /
            # `datetime.date.today()`.
            return isinstance(node.func.value, ast.Name | ast.Attribute)
        if node.func.attr == "date":
            # `datetime.now().date()` — naive, so local. `now(UTC)` is fine, and
            # so is `.date()` on a tz-aware column (`run.executed_at.date()`).
            inner = node.func.value
            return (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "now"
                and not inner.args
                and not inner.keywords
            )
        return False

    tree = ast.parse(source, filename=filename)
    return [node.lineno for node in ast.walk(tree) if _is_local_today(node)]


def test_utc_today_is_the_utc_calendar_date():
    assert utc_today() == datetime.now(UTC).date()
    assert isinstance(utc_today(), date)


@pytest.mark.parametrize(
    "snippet",
    [
        "date.today()",
        "datetime.today()",
        "_dt.today()",
        "datetime.date.today()",
        "x = foo or datetime.date.today()",
        # Naive `now()` → a local date, under a name that reads deliberate.
        "datetime.now().date()",
        "datetime.datetime.now().date()",
    ],
)
def test_scanner_catches_every_local_today_spelling(snippet):
    """The scanner's own regression test.

    `datetime.date.today()` slipped past the first version of this guard, which
    is how two Positive Pay modules stayed on local time while the guard read
    green. `datetime.now().date()` is the same trap one step further out — it
    isn't spelled `today` at all. Pin every spelling, whether or not `app/`
    currently contains it: the ones it doesn't are exactly the ones a future
    edit reaches for after the obvious spelling starts failing.
    """
    assert local_today_call_lines(snippet) == [1]


@pytest.mark.parametrize(
    "snippet",
    [
        "utc_today()",
        "datetime.now(UTC).date()",
        "datetime.now(tz=UTC).date()",
        "datetime.now(UTC)",
        # `.date()` on a tz-aware COLUMN is the right way to read a stored
        # instant's calendar day — `services/positive_pay` does exactly this
        # for `run.executed_at`, with utc_today() as the fallback.
        "run.executed_at.date()",
        "# date.today() is wrong here, see the module docstring",
        '"""Never call date.today() in this module."""',
    ],
)
def test_scanner_ignores_the_correct_forms_and_prose(snippet):
    assert local_today_call_lines(snippet) == []


@pytest.mark.parametrize("relative", UTC_TODAY_MODULES)
def test_module_never_reads_the_servers_local_today(relative):
    """Fails on any local-timezone "today" read in a converted module.

    If this fires, import `utc_today` from `app/utils/dates.py` (or inline
    `datetime.now(UTC).date()`) rather than reaching for the local-timezone
    call — the two agree only while the host happens to run UTC.
    """
    path = APP_DIR / relative
    assert path.exists(), f"{relative} moved — update UTC_TODAY_MODULES"

    offenders = local_today_call_lines(path.read_text(encoding="utf-8"), filename=str(path))

    assert not offenders, (
        f"{relative} reads the server's local 'today' at line(s) {offenders}. "
        "Use app.utils.dates.utc_today() — these modules feed the cash-flow "
        "horizon, the plan_id hash, the scheduled-report window, the early-pay "
        "discount cutoff, the recurring-template period key and the regulated "
        "approval_date, all of which must agree with each other on a non-UTC "
        "host."
    )


@pytest.mark.parametrize("relative", UTC_TODAY_TEST_MODULES)
def test_test_module_never_anchors_on_the_servers_local_today(relative):
    """Fails on any local-timezone "today" read in a converted TEST module.

    If this fires, import `utc_today` from `app/utils/dates.py` in the test
    too. The assertion is only as correct as the clock it samples: the code
    under test reads the UTC date, so a fixture anchored on the local date is a
    day off for part of every day on a non-UTC host — and lands precisely on
    the boundaries these tests exist to pin.

    Reproduce the whole class deterministically with
    `TZ=Pacific/Kiritimati pytest -q` rather than waiting for a clock.
    """
    path = TESTS_DIR / relative
    assert path.exists(), f"{relative} moved — update UTC_TODAY_TEST_MODULES"

    offenders = local_today_call_lines(path.read_text(encoding="utf-8"), filename=str(path))

    assert not offenders, (
        f"{relative} anchors on the server's local 'today' at line(s) "
        f"{offenders}. Use app.utils.dates.utc_today() — the module under test "
        "resolves today in UTC, so a local-date fixture disagrees with it for "
        "hours every day off a UTC host, and CI (which runs UTC) will not tell "
        "you."
    )
