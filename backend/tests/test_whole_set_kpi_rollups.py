"""A list's KPI rollup must describe the same set as the table beneath it.

Six list pages shipped a KPI row computed over the rows already LOADED (PR
#349), and `GET /api/invoices/counts` ignored the list's filters entirely (PR
#352). Both produce the same lie: a headline that contradicts the table under
it as soon as the population is larger than one page or narrower than the whole
tenant.

The fix in every case was a shared `_*_list_filters` builder called by both the
list endpoint and its `/summary`. This file guards that shape three ways:

  * **Discovery** — every `GET .../summary` paired with a list parent is
    checked, so a NEW list page with a KPI is covered without editing this
    file. Exemptions must be named and justified below, not silently skipped.
  * **Contract** — the summary must accept every filter its list offers, read
    off the mounted OpenAPI schema rather than the function signature. A
    parameter declared as a plain default (`search: str | None = None`) is a
    real query parameter but is NOT a `fastapi.params.Query` instance, so a
    signature-level check misses it and passes vacuously.
  * **Wiring** — both endpoints in each module must route through the shared
    filter builder, so neither can hand-roll a divergent set of predicates.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.main import app

# Paging / ordering shape the VIEW, not the population, so a rollup over the
# whole set has no use for them.
_VIEW_ONLY_PARAMS = {"page", "page_size", "sort", "order"}

# Summary endpoints that deliberately describe a WIDER set than any filtered
# table, with the reason. Each is called from the frontend with no query
# parameters at all, so there is no on-screen contradiction to fix — but a
# future caller passing filters would find they do not exist, which is why they
# are recorded here rather than dropped from discovery.
_DELIBERATELY_WHOLE_SET = {
    "/api/payments/summary": (
        "The payments KPI bar is a whole-entity treasury figure (money totals "
        "in the reporting currency), not a caption for the filtered table. "
        "`frontend/src/routes/payments/+page.svelte::loadSummary` calls it with "
        "no parameters."
    ),
    "/api/exceptions/summary": (
        "Its counts POPULATE the filter chips, so they must span every "
        "type/severity/status rather than being narrowed by the chip currently "
        "selected — the same reason `GET /api/invoices/counts` ignores a "
        "`status` param. "
        "`frontend/src/routes/exceptions/+page.svelte::loadSummary` calls it "
        "with no parameters."
    ),
}


def _openapi_query_params(spec, path: str) -> set[str] | None:
    """Query-parameter names FastAPI actually resolves for `GET path`.

    Read from the generated schema rather than `inspect.signature`, because a
    scalar parameter with a plain default is a query parameter to FastAPI but
    is not a `fastapi.params.Query` instance — the distinction that would make
    a signature-level assertion silently vacuous.
    """
    operation = spec["paths"].get(path, {}).get("get")
    if operation is None:
        return None
    return {p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"}


def _rollup_pairs():
    """(list_path, summary_path) for every discovered list-plus-KPI surface."""
    spec = app.openapi()
    pairs = []
    for path in sorted(spec["paths"]):
        if not path.endswith("/summary"):
            continue
        parent = path[: -len("/summary")]
        # A summary hanging off a single record (`/invoices/{id}/summary`) is a
        # detail view, not a rollup over a filtered list.
        if "{" in path:
            continue
        if _openapi_query_params(spec, parent) is None:
            continue  # no GET list parent — not a list rollup
        pairs.append((parent, path))
    return spec, pairs


def test_discovery_finds_the_known_rollup_surfaces():
    """Guards the guard: if discovery silently stopped matching, every
    parametrised case below would vanish and this file would pass empty."""
    _, pairs = _rollup_pairs()
    found = {summary for _, summary in pairs}
    for expected in (
        "/api/budgets/summary",
        "/api/expenses/summary",
        "/api/intake/summary",
        "/api/positive-pay/summary",
        "/api/recurring/summary",
        "/api/requisitions/summary",
        "/api/vendor-statements/summary",
    ):
        assert expected in found, f"{expected} is no longer discovered as a list rollup"


def test_every_exemption_still_corresponds_to_a_real_surface():
    """An exemption for a surface that no longer exists is a stale excuse that
    would silently cover a future endpoint reusing the path."""
    _, pairs = _rollup_pairs()
    found = {summary for _, summary in pairs}
    for exempt in _DELIBERATELY_WHOLE_SET:
        assert exempt in found, f"{exempt} is exempted but no longer discovered"


@pytest.mark.parametrize("pair", _rollup_pairs()[1], ids=lambda p: p[1])
def test_a_rollup_accepts_every_filter_its_list_offers(pair):
    """The KPI must be able to describe exactly the set the table shows.

    A filter the list has and the summary does not means the headline spans a
    wider population than the rows beneath it, which is the defect PR #349 and
    PR #352 each fixed on their own surfaces.
    """
    list_path, summary_path = pair
    spec = app.openapi()
    list_params = _openapi_query_params(spec, list_path) - _VIEW_ONLY_PARAMS
    summary_params = _openapi_query_params(spec, summary_path)

    missing = sorted(list_params - summary_params)
    if summary_path in _DELIBERATELY_WHOLE_SET:
        assert missing, (
            f"{summary_path} is listed in _DELIBERATELY_WHOLE_SET but now accepts "
            "every filter its list offers — delete the exemption rather than "
            "leaving a stale excuse in place."
        )
        return

    assert not missing, (
        f"{summary_path} cannot take {missing}, which {list_path} offers, so the "
        "KPI row would describe a wider set than the table beneath it. Either "
        "thread the filter through the shared filter builder, or add an entry "
        "to _DELIBERATELY_WHOLE_SET explaining why the wider set is correct."
    )


# ---------------------------------------------------------------------------
# Wiring — both endpoints must go through the one shared filter builder
# ---------------------------------------------------------------------------

_SHARED_BUILDER_MODULES = {
    "app/api/budgets.py": ("_budget_list_filters", "list_budgets", "budget_summary"),
    "app/api/expenses.py": ("_expense_list_filters", "list_expenses", "expense_summary"),
    "app/api/intake.py": ("_intake_list_filters", "list_intake", "intake_summary"),
    "app/api/positive_pay.py": (
        "_positive_pay_list_filters",
        "list_files",
        "positive_pay_summary",
    ),
    "app/api/recurring.py": ("_recurring_list_filters", "list_templates", "template_summary"),
    "app/api/requisitions.py": (
        "_requisition_list_filters",
        "list_requisitions",
        "requisition_summary",
    ),
    "app/api/vendor_statement_recon.py": (
        "_recon_list_filters",
        "list_reconciliations",
        "reconciliation_summary",
    ),
}


def _functions_calling(source: str, builder: str) -> set[str]:
    """Top-level function names whose body calls `builder`."""
    tree = ast.parse(source)
    callers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == builder
            ):
                callers.add(node.name)
    return callers


@pytest.mark.parametrize("module_path", sorted(_SHARED_BUILDER_MODULES))
def test_the_list_and_its_rollup_share_one_filter_builder(module_path):
    """Accepting the same parameters is not enough — both endpoints have to
    apply them through the SAME code, or the two can still diverge on what a
    given filter means (which columns a search covers, say)."""
    builder, list_fn, summary_fn = _SHARED_BUILDER_MODULES[module_path]
    source = (pathlib.Path(__file__).resolve().parents[1] / module_path).read_text()

    assert f"def {builder}(" in source, f"{module_path} no longer defines {builder}"

    callers = _functions_calling(source, builder)
    for fn in (list_fn, summary_fn):
        assert fn in callers, (
            f"{module_path}::{fn} does not call {builder}. The list and its KPI "
            "rollup must apply filters through the one shared builder so they "
            "cannot drift on what a filter means."
        )
