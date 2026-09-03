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

`app/utils/dates.utc_today()` is the one definition, and **the scan is now the
whole of `app/`** — no allowlist. It got there in two moves: the conversion
waves (the cash-flow stack, then the AP surfaces whose "today" is a comparison
rather than a label — the discount deadline, the past-due and future-date fraud
flags, the recurring period key that IS the generation idempotency guard, the
regulated `Invoice.approval_date`, plus the stamps and filenames that sit
beside a `datetime.now(UTC)` in the same response), and then the last six
modules that resolved UTC correctly but *inlined* `datetime.now(UTC).date()`
instead of importing the helper.

The allowlist was the right shape while the tree was mixed — it is how a module
got converted, one at a time, without the guard going red on the unconverted
rest. It is the wrong shape now that nothing under `app/` reads the local date:
an opt-in list cannot see a NEW module, and a new module is precisely where the
next `date.today()` arrives. A whole-tree scan has no such gap, and it costs
nothing to maintain.

`test_no_module_inlines_the_utc_today_expression` is the second half. A module
spelling `datetime.now(UTC).date()` is *correct*, so the local-today scan can
never flag it — but it is one careless edit (dropping the `UTC` argument) from
being silently wrong, and the edited line still reads as deliberate. One owner
removes the class rather than policing it.

Shape borrowed from `tests/test_payment_methods.py`'s source scan.
"""

from __future__ import annotations

import ast
import os
import pathlib
import time
from datetime import UTC, date, datetime

import pytest

from app.utils.dates import utc_today

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
TESTS_DIR = pathlib.Path(__file__).resolve().parent

#: Modules exempt from the whole-`app/` scan below. Empty, and it should stay
#: that way: an exemption here is a module that reads the SERVER's local
#: calendar date on purpose, which nothing in a multi-tenant backend has a
#: reason to do. (`scripts/seed*.py` still uses the local date deliberately —
#: demo fixtures on a dev laptop have nothing to reconcile against — but
#: `scripts/` is outside `app/` and outside this scan.)
LOCAL_TODAY_EXEMPT: tuple[str, ...] = ()

#: The one module allowed to spell the expression `utc_today()` wraps.
UTC_TODAY_OWNER = "utils/dates.py"

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


@pytest.mark.parametrize("tz", ["Pacific/Kiritimati", "Pacific/Midway", "Asia/Tokyo", "UTC"])
def test_utc_today_ignores_the_process_timezone(tz):
    """The behavioural half of this file: everything else here is a source scan.

    `Pacific/Kiritimati` is UTC+14 and `Pacific/Midway` UTC-11, so between them
    the local calendar date is a day ahead of / behind UTC for most of the
    24-hour cycle — which is the whole failure mode, reproduced deterministically
    instead of waited for. Whatever the host is set to, `utc_today()` answers the
    UTC date; on the two skewed zones it also, for most of the day, disagrees
    with `date.today()`, which is the disagreement the scans exist to prevent
    anyone reintroducing.

    `TZ` + `tzset()` is process-global, so the original value is restored in a
    `finally` — a leaked timezone would silently re-point every date-sensitive
    test that runs after this one.
    """
    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = tz
        time.tzset()
        assert utc_today() == datetime.now(UTC).date()
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


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


def test_no_module_under_app_reads_the_servers_local_today():
    """Fails on any local-timezone "today" read anywhere under `app/`.

    If this fires, import `utc_today` from `app/utils/dates.py` rather than
    reaching for the local-timezone call — the two agree only while the host
    happens to run UTC, and these modules feed the cash-flow horizon, the
    plan_id hash, the scheduled-report window, the early-pay discount cutoff,
    the recurring-template period key and the regulated `approval_date`, all of
    which must agree with each other on a non-UTC host.

    Whole-tree, deliberately: the allowlist this replaced could not see a
    module nobody had added to it, and a brand-new module is exactly where the
    next `date.today()` shows up.
    """
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        relative = str(path.relative_to(APP_DIR))
        if relative in LOCAL_TODAY_EXEMPT:
            continue
        lines = local_today_call_lines(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(f"{relative}:{line}" for line in lines)

    assert not offenders, (
        f"local-timezone 'today' read at {offenders}. Use "
        "app.utils.dates.utc_today() — `date.today()` (and naive "
        "`datetime.now().date()`) resolve in the SERVER's timezone, which "
        "matches UTC only by accident of where the container runs."
    )


def test_no_module_inlines_the_utc_today_expression():
    """`datetime.now(UTC).date()` has exactly one home.

    Inlining it is *correct*, which is what makes it worth removing: the
    local-today scan above cannot flag it, so a later edit that drops the `UTC`
    argument turns a right line into a wrong one with nothing to catch it and
    nothing in the diff that looks like a timezone decision. Six modules sat in
    that state — `api/api_keys`, `api/bank_reconciliation`, the recurring /
    contract-renewal / discount auto-capture sweeps and the mock financing
    adapter — alongside `api/cash_flow` and the copilot tools that predated the
    helper.
    """
    needle = "now(UTC).date()"
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in sorted(APP_DIR.rglob("*.py"))
        if str(path.relative_to(APP_DIR)) != UTC_TODAY_OWNER
        and needle in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{offenders} inline `datetime.now(UTC).date()`. Import "
        "`utc_today` from app.utils.dates instead — one owner means a "
        "timezone change is one edit, and a dropped `UTC` argument cannot "
        "hide inside a line that already looked deliberate."
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
