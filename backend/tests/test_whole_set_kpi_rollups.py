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
import re

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


#: How many list-plus-KPI surfaces discovery is known to find. Discovery feeds
#: `@pytest.mark.parametrize`, and a parametrisation over an EMPTY list produces
#: no test at all — pytest reports nothing rather than failing. That is exactly
#: how the guard this file replaced managed to pass while checking nothing, so
#: the count is asserted rather than trusted.
_KNOWN_ROLLUP_COUNT = 9


def test_discovery_cannot_silently_find_nothing():
    """The parametrised cases below only exist if discovery matched something.

    `test_discovery_finds_the_known_rollup_surfaces` names the surfaces it
    expects; this asserts the *shape* of the discovery itself — a non-empty
    list, of at least the size we know about — so a change to `_rollup_pairs`
    that stops matching (a router prefix change, a `/summary` renamed to
    `/rollup`) fails here instead of quietly emptying the parametrisation.
    """
    _, pairs = _rollup_pairs()
    assert pairs, "discovery matched no list-plus-KPI surfaces at all"
    assert len(pairs) >= _KNOWN_ROLLUP_COUNT, (
        f"discovery found {len(pairs)} rollup surfaces, fewer than the "
        f"{_KNOWN_ROLLUP_COUNT} already known — a surface stopped being "
        "discovered, which would silently drop it from every check below."
    )
    # Every discovered pair is a real (list, summary) path pair, not a
    # half-resolved entry that would make the checks below no-ops.
    for list_path, summary_path in pairs:
        assert summary_path == f"{list_path}/summary"
        assert list_path.startswith("/api/")


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


def _router_prefixes(module_path: str) -> set[str]:
    """Every `APIRouter(prefix="...")` literal declared in ``module_path``.

    Used to tie a discovered `/api/<x>/summary` back to the module that owns
    it WITHOUT walking FastAPI's internal route tree (whose shape is a private
    implementation detail that has changed across versions). Same
    read-the-source approach the wiring assertion above already takes.
    """
    source = (pathlib.Path(__file__).resolve().parents[1] / module_path).read_text()
    return set(re.findall(r'APIRouter\(prefix="([^"]+)"', source))


def test_every_discovered_rollup_is_covered_by_a_wiring_check():
    """A NEW list page with a KPI must not be able to skip the wiring axis.

    Discovery makes the *contract* check automatic — a new `/summary` is
    checked for filter parity without editing this file. The wiring check is
    driven by the hand-maintained `_SHARED_BUILDER_MODULES` map instead, so a
    new surface accepting the right filter names while hand-rolling its own
    divergent predicates would pass everything here. This closes that gap:
    every discovered non-exempt rollup has to belong to a module in the map.
    """
    _, pairs = _rollup_pairs()
    covered = set()
    for module_path in _SHARED_BUILDER_MODULES:
        covered |= {f"/api{prefix}" for prefix in _router_prefixes(module_path)}

    uncovered = sorted(
        list_path
        for list_path, summary_path in pairs
        if summary_path not in _DELIBERATELY_WHOLE_SET and list_path not in covered
    )
    assert not uncovered, (
        f"{uncovered} have a discovered KPI rollup but no entry in "
        "_SHARED_BUILDER_MODULES, so nothing checks that the list and its "
        "rollup apply filters through ONE shared builder. Add the module (and "
        "its builder / list / summary function names) to the map."
    )


# ---------------------------------------------------------------------------
# `/counts` surfaces — the same parity contract, spelled out by decisions §48
# ---------------------------------------------------------------------------
#
# `/summary` was guarded above; `/counts` was not, and that is not a cosmetic
# gap. Two live violations of §48 were found by hand in one session — the
# payments History chips tallied the whole entity while the list was searched,
# and the vendors chips hand-rolled a predicate set that had already diverged
# on `source` — precisely because neither endpoint's path ends in `/summary`.
#
# §48 states the contract a counts endpoint owes its list. It has three parts,
# and each is asserted below:
#
#   1. it takes every NARROWING filter the list takes, so the chips describe
#      the rows the table shows;
#   2. it deliberately does NOT take `status`, because status is the dimension
#      being tallied — applying it returns the selected status' count and zero
#      for every other chip, the exact "chip that lies" failure the endpoint
#      exists to prevent;
#   3. its RBAC matches the list's EXACTLY, in both directions. Tighter and a
#      caller sees rows above chips that cannot explain them (and the page
#      falls back to the page-scoped tally the endpoint replaced). Looser and a
#      role deliberately kept out of the queue can still read its size.
#
# Discovery is automatic: the handler name comes off the OpenAPI `operationId`,
# so a NEW `/counts` endpoint is checked without editing this file.

#: The dimension a status-counts endpoint tallies. It must not accept this as a
#: filter — see §48 and `invoices.py::invoice_counts`.
_TALLIED_DIMENSION = "status"


def _handler_name(spec, path: str) -> str | None:
    """The handler function's name, from FastAPI's generated `operationId`.

    FastAPI builds it as `<function>_<path with separators flattened>_<method>`,
    so stripping the generated suffix recovers the function name — which is what
    lets the RBAC axis below be discovery-driven rather than a hand-maintained
    map that a new endpoint could simply be left out of.
    """
    operation = spec["paths"].get(path, {}).get("get")
    if operation is None:
        return None
    op_id = operation.get("operationId")
    if not op_id:
        return None
    suffix = (
        "_"
        + path.strip("/")
        .replace("/", "_")
        .replace("-", "_")
        .replace("{", "")
        .replace("}", "")
        .replace("__", "_")
        + "_get"
    )
    return op_id[: -len(suffix)] if op_id.endswith(suffix) else None


def _counts_pairs():
    """(list_path, counts_path) for every discovered status-counts surface."""
    spec = app.openapi()
    pairs = []
    for path in sorted(spec["paths"]):
        if not path.endswith("/counts") or "{" in path:
            continue
        parent = path[: -len("/counts")]
        if _openapi_query_params(spec, parent) is None:
            continue  # no GET list parent — not a list's chip tally
        pairs.append((parent, path))
    return spec, pairs


#: Same non-vacuity guard the `/summary` discovery carries: a parametrisation
#: over an empty list runs zero tests and reports success.
_KNOWN_COUNTS_SURFACES = {
    "/api/invoices/counts",
    "/api/payments/counts",
    "/api/purchase-orders/counts",
    "/api/vendors/counts",
    "/api/vendors/change-requests/counts",
}


def test_counts_discovery_finds_every_known_surface():
    _, pairs = _counts_pairs()
    found = {counts for _, counts in pairs}
    assert pairs, "discovery matched no status-counts surfaces at all"
    missing = sorted(_KNOWN_COUNTS_SURFACES - found)
    assert not missing, f"no longer discovered as a list's counts surface: {missing}"


def test_every_discovered_counts_surface_resolves_its_handler():
    """The RBAC axis reads the handler off `operationId`. If FastAPI ever
    changes that format the axis would silently check nothing, so the
    resolution itself is asserted rather than assumed."""
    spec, pairs = _counts_pairs()
    for list_path, counts_path in pairs:
        for path in (list_path, counts_path):
            assert _handler_name(spec, path), (
                f"could not resolve a handler name for {path} from its operationId "
                f"({spec['paths'][path]['get'].get('operationId')!r}) — the RBAC "
                "check below would skip it"
            )


@pytest.mark.parametrize("pair", _counts_pairs()[1], ids=lambda p: p[1])
def test_a_counts_endpoint_takes_every_narrowing_filter_its_list_offers(pair):
    """§48, part 1 — the chips must describe the rows the table shows.

    A filter the list has and the tally does not means the chips span a wider
    population than the rows beneath them: searching one vendor left the
    payments History chips reading the tenant's whole total over a one-row
    table.
    """
    list_path, counts_path = pair
    spec = app.openapi()
    required = _openapi_query_params(spec, list_path) - _VIEW_ONLY_PARAMS - {_TALLIED_DIMENSION}
    counts_params = _openapi_query_params(spec, counts_path)

    missing = sorted(required - counts_params)
    assert not missing, (
        f"{counts_path} cannot take {missing}, which {list_path} offers, so the "
        f"chips would describe a wider set than the table beneath them. Thread "
        f"the filter through the shared filter builder both endpoints call."
    )


@pytest.mark.parametrize("pair", _counts_pairs()[1], ids=lambda p: p[1])
def test_a_counts_endpoint_does_not_take_the_dimension_it_tallies(pair):
    """§48, part 2 — accepting `status` would let a caller zero every chip.

    The endpoint groups BY status; narrowing by it returns the selected status'
    count and nothing else, so the other chips read zero while the table shows
    rows. FastAPI drops an undeclared query param, so a client that passes one
    is unaffected — which is only true while it stays undeclared.
    """
    _, counts_path = pair
    spec = app.openapi()
    assert _TALLIED_DIMENSION not in _openapi_query_params(spec, counts_path), (
        f"{counts_path} declares `{_TALLIED_DIMENSION}`, the dimension it tallies. "
        "Applying it returns one chip's count and zero for the rest — the 'chip "
        "that lies' failure the endpoint exists to prevent (decisions §48)."
    )


def _auth_dependency(module_source: str, fn_name: str) -> str | None:
    """The `Depends(...)` expression gating `fn_name`, normalised to source text.

    Compared as text on purpose: two gates that are spelled differently ARE
    different gates as far as a caller is concerned, and the failure this
    catches is exactly a spelling drift between two endpoints that must agree.
    """
    tree = ast.parse(module_source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != fn_name:
            continue
        defaults = list(node.args.defaults or []) + [
            d for d in (node.args.kw_defaults or []) if d is not None
        ]
        for default in defaults:
            expr = ast.unparse(default)
            if "Depends(" in expr and any(
                gate in expr for gate in ("require_roles", "require_permission", "get_current_user")
            ):
                return expr
    return None


_API_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"


def _module_defining(*fn_names: str) -> pathlib.Path | None:
    """The api module that defines all of ``fn_names`` at top level.

    Located by DEFINITION rather than by router prefix: more than one module
    can mount a router on the same prefix (`email_actions.py` also serves
    `/invoices`), so prefix matching is ambiguous where this is exact.
    """
    wanted = set(fn_names)
    for module in sorted(_API_DIR.glob("*.py")):
        tree = ast.parse(module.read_text())
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if wanted <= defined:
            return module
    return None


@pytest.mark.parametrize("pair", _counts_pairs()[1], ids=lambda p: p[1])
def test_a_counts_endpoint_is_gated_exactly_like_its_list(pair):
    """§48, part 3 — RBAC matches the list, in BOTH directions.

    Tighter than the list and a caller who can read the rows gets a 403 on the
    chips, at which point the page falls back to the page-scoped tally this
    endpoint exists to replace — reintroducing the undercount for exactly that
    caller. Looser and a role deliberately excluded from the queue can still
    read its size, which for the vendor bank-change queue is the number of
    staged bank redirects awaiting review.
    """
    list_path, counts_path = pair
    spec = app.openapi()
    list_fn = _handler_name(spec, list_path)
    counts_fn = _handler_name(spec, counts_path)
    module = _module_defining(list_fn, counts_fn)
    assert module is not None, (
        f"no api module defines both {list_fn} and {counts_fn} — a list and its "
        "counts endpoint living in different modules would need a different lookup"
    )
    source = module.read_text()

    list_gate = _auth_dependency(source, list_fn)
    counts_gate = _auth_dependency(source, counts_fn)

    assert list_gate, f"{module.name}::{list_fn} has no recognisable auth dependency"
    assert counts_gate, f"{module.name}::{counts_fn} has no recognisable auth dependency"
    assert counts_gate == list_gate, (
        f"{counts_path} is gated `{counts_gate}` while {list_path} is gated "
        f"`{list_gate}`. decisions §48 requires them to match exactly: a tally "
        "reachable by more callers than the rows it counts leaks the size of a "
        "set they cannot see, and one reachable by fewer leaves the page showing "
        "rows above chips that cannot explain them."
    )
