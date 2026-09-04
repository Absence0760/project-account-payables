"""Money **filter bounds** are `Decimal`, never `float` — plus a drift guard.

`amount_min` / `amount_max` on the invoice and payment lists (and `materiality`
on the vendor-statement close-readiness gate) were declared `float | None` and
then converted with `Decimal(str(value))` before reaching a `Numeric(15, 2)`
column. `Decimal(str(f))` recovers the shortest repr, so an ordinary two-decimal
bound round-trips — which is why this never produced a visible bug — but a bound
given to more precision than a double holds is silently re-rounded on the way
in, and a row sitting exactly on the boundary then falls the WRONG side of the
filter. Root `CLAUDE.md` § Project invariants states "money is exact" with no
carve-out for query bounds, and these bounds are compared against exact
`Numeric` columns.

Retyping the parameter `Decimal | None` is necessary but NOT sufficient, which
is the part worth remembering. FastAPI hands a query parameter to pydantic as
the raw string, so the exact value now reaches the query builder — but
SQLAlchemy types a comparison's bind parameter from the column and the asyncpg
dialect renders a bind cast, `invoices.amount >= $1::NUMERIC(15, 2)`, so
Postgres rounds the over-precise bound back onto the boundary anyway. The bound
is therefore snapped onto the column's own 2dp grid in the direction of the
comparison (`app/api/money_filters.py`): a lower bound rounds UP, an upper bound
rounds DOWN. Rounding to nearest is never right — the comparison's direction
decides which way is safe.

Four things are guarded here:

  * **Behaviour** — a high-precision bound selects/excludes the boundary row
    correctly on both the invoice and the payment list.
  * **Parity** — a list and its `/counts` rollup agree on that same bound,
    proving both sides of the shared `_*_list_filters` builder moved together.
    A one-sided fix is exactly the drift those builders exist to prevent.
  * **Snapping** — the grid snap preserves the set in both directions, and the
    bind cast that makes it necessary is asserted rather than assumed.
  * **Drift** — no NEW money bound can be added as a `float`. Two independent
    scans: the mounted OpenAPI schema (catches a route parameter) and an AST
    source scan of `app/api/` (catches the shared private builders, which never
    appear in OpenAPI at all). Each scan is exercised against a deliberately
    offending fixture, so a scan that stopped detecting anything fails here.
"""

from __future__ import annotations

import ast
import pathlib
import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.money_filters import snap_lower_bound, snap_upper_bound
from app.main import app
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment

TENANT = "a"

# A bound one ULP-ish beyond what a double can hold. `float("100.00000000000000001")`
# is exactly 100.0, so a bound routed through a float widens `>= this` into
# `>= 100.00` and admits the boundary row it was written to exclude.
ABOVE_100 = "100.00000000000000001"
# The mirror case: `float("99.99999999999999999")` is also exactly 100.0, so a
# float-routed `<= this` admits a 100.00 row it was written to exclude.
BELOW_100 = "99.99999999999999999"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


def test_the_chosen_bounds_really_do_collapse_through_a_float():
    """Guards the guard: if these literals became float-exact the behaviour
    tests below would pass against the pre-fix code and prove nothing."""
    assert float(ABOVE_100) == 100.0
    assert float(BELOW_100) == 100.0
    assert Decimal(ABOVE_100) > Decimal("100.00")
    assert Decimal(BELOW_100) < Decimal("100.00")
    # The pre-fix conversion hop, spelled out: it lands on the boundary itself.
    assert Decimal(str(float(ABOVE_100))) == Decimal("100.0")
    assert Decimal(str(float(BELOW_100))) == Decimal("100.0")


# ---------------------------------------------------------------------------
# Behaviour — invoices
# ---------------------------------------------------------------------------


async def _seed_invoices(realdb, prefix: str) -> None:
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        ent = await _default_entity_id(s)
        for n, amount in ((1, "100.00"), (2, "250.00")):
            s.add(
                Invoice(
                    organization_id=org_id,
                    entity_id=ent,
                    invoice_number=f"{prefix}-{n}",
                    vendor_name=f"{prefix} Vendor",
                    amount=Decimal(amount),
                    currency="USD",
                    status=InvoiceStatus.approved,
                )
            )
        await s.commit()


@pytest.mark.asyncio
async def test_invoice_amount_min_excludes_the_boundary_row(realdb):
    """`amount_min` just above 100.00 must NOT return the 100.00 invoice."""
    await _seed_invoices(realdb, "MONEYMIN")
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(f"/api/invoices?search=MONEYMIN&amount_min={ABOVE_100}")
    assert resp.status_code == 200, resp.text
    numbers = sorted(i["invoice_number"] for i in resp.json()["items"])
    assert numbers == ["MONEYMIN-2"], (
        "a bound of 100.00000000000000001 collapsed to 100.0 and admitted the "
        "boundary invoice — the money bound is being routed through a float"
    )


@pytest.mark.asyncio
async def test_invoice_amount_max_excludes_the_boundary_row(realdb):
    """`amount_max` just below 100.00 must NOT return the 100.00 invoice."""
    await _seed_invoices(realdb, "MONEYMAX")
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(f"/api/invoices?search=MONEYMAX&amount_max={BELOW_100}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == [], (
        "a bound of 99.99999999999999999 collapsed to 100.0 and admitted the boundary invoice"
    )


@pytest.mark.asyncio
async def test_invoice_list_counts_and_ids_agree_on_the_same_bound(realdb):
    """All three consumers of `_invoice_list_filters` must resolve one set.

    The list, the `/counts` chips and the `/ids` "select all N matching"
    resolver share the builder precisely so they cannot describe different
    populations. Retyping only the list's parameter would have re-opened that
    gap for any high-precision bound.
    """
    await _seed_invoices(realdb, "MONEYTRI")
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        params = f"search=MONEYTRI&amount_min={ABOVE_100}"
        listed = (await c.get(f"/api/invoices?{params}")).json()
        counts = (await c.get(f"/api/invoices/counts?{params}")).json()
        ids = (await c.get(f"/api/invoices/ids?{params}")).json()

    assert listed["total"] == 1
    assert counts["total"] == listed["total"], (
        "the chips and the table disagree on a high-precision amount bound — "
        "one side of the shared filter builder is still float-typed"
    )
    assert len(ids["ids"]) == listed["total"], (
        '"select all N matching" resolved a different set than the table for the same amount bound'
    )


# ---------------------------------------------------------------------------
# Behaviour — payments
# ---------------------------------------------------------------------------


async def _seed_payments(realdb, prefix: str) -> None:
    """One payment per invoice — `uq_payments_one_live_per_invoice` forbids
    stacking live payments on a single invoice."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        ent = await _default_entity_id(s)
        for n, amount in ((1, "100.00"), (2, "250.00")):
            inv = Invoice(
                organization_id=org_id,
                entity_id=ent,
                invoice_number=f"{prefix}-{n}",
                vendor_name=f"{prefix} Vendor",
                amount=Decimal(amount),
                currency="USD",
                status=InvoiceStatus.approved,
            )
            s.add(inv)
            await s.flush()
            s.add(
                Payment(
                    invoice_id=inv.id,
                    entity_id=ent,
                    amount=Decimal(amount),
                    status="completed",
                    method="ach",
                    reference=f"{prefix}-REF-{n}",
                )
            )
        await s.commit()


@pytest.mark.asyncio
async def test_payment_amount_min_excludes_the_boundary_row(realdb):
    await _seed_payments(realdb, "PMONEYMIN")
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(f"/api/payments?search=PMONEYMIN&amount_min={ABOVE_100}")
    assert resp.status_code == 200, resp.text
    amounts = sorted(str(i["amount"]) for i in resp.json()["items"])
    assert resp.json()["total"] == 1, (
        "a bound of 100.00000000000000001 collapsed to 100.0 and admitted the "
        "boundary payment — the money bound is being routed through a float"
    )
    assert amounts == ["250.0"] or amounts == ["250.00"], amounts


@pytest.mark.asyncio
async def test_payment_amount_max_excludes_the_boundary_row(realdb):
    await _seed_payments(realdb, "PMONEYMAX")
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get(f"/api/payments?search=PMONEYMAX&amount_max={BELOW_100}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0, (
        "a bound of 99.99999999999999999 collapsed to 100.0 and admitted the boundary payment"
    )


@pytest.mark.asyncio
async def test_payment_list_and_counts_agree_on_the_same_bound(realdb):
    """`GET /api/payments` and `GET /api/payments/counts` share
    `_payment_list_filters`; both sides had to move together."""
    await _seed_payments(realdb, "PMONEYTRI")
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        params = f"search=PMONEYTRI&amount_min={ABOVE_100}"
        listed = (await c.get(f"/api/payments?{params}")).json()
        counts = (await c.get(f"/api/payments/counts?{params}")).json()

    assert listed["total"] == 1
    assert counts["total"] == listed["total"], (
        "the History chips and the table disagree on a high-precision amount "
        "bound — one side of the shared filter builder is still float-typed"
    )


# ---------------------------------------------------------------------------
# Snapping — why typing the parameter was not on its own enough
# ---------------------------------------------------------------------------


def test_the_bind_cast_that_makes_snapping_necessary_is_still_rendered():
    """The bound is cast to the COLUMN's scale before Postgres compares it.

    This is the non-obvious half of the bug: with the parameter correctly typed
    `Decimal`, SQLAlchemy still types the bind parameter from the column and the
    asyncpg dialect renders `>= $1::NUMERIC(15, 2)`, which rounds an over-precise
    bound to nearest — straight back onto the boundary row. If SQLAlchemy ever
    stops emitting that cast the snap becomes redundant rather than wrong, but
    the reasoning in `api/money_filters` would need revisiting, so assert it.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    compiled = str(
        select(Invoice.id)
        .where(Invoice.amount >= Decimal(ABOVE_100))
        .compile(dialect=postgresql.asyncpg.dialect())
    )
    assert "NUMERIC(15, 2)" in compiled


def test_snapping_moves_a_bound_onto_the_grid_without_changing_the_set():
    """A money column is a whole number of cents, so the only values a bound can
    ever admit or exclude sit on that grid. Snapping picks the nearest grid
    point that keeps the comparison's meaning."""
    # Lower bound rounds UP: nothing on the grid lies in (100.00, 100.01).
    assert snap_lower_bound(Decimal(ABOVE_100), Invoice.amount) == Decimal("100.01")
    assert snap_lower_bound(Decimal("100.005"), Invoice.amount) == Decimal("100.01")
    # Upper bound rounds DOWN — the mirror.
    assert snap_upper_bound(Decimal(BELOW_100), Invoice.amount) == Decimal("99.99")
    assert snap_upper_bound(Decimal("100.005"), Invoice.amount) == Decimal("100.00")
    # A bound already on the grid is untouched, in both directions.
    for bound in ("100.00", "0.01", "-5.25"):
        assert snap_lower_bound(Decimal(bound), Invoice.amount) == Decimal(bound)
        assert snap_upper_bound(Decimal(bound), Invoice.amount) == Decimal(bound)
    # Negatives round by direction, not by magnitude.
    assert snap_lower_bound(Decimal("-100.001"), Invoice.amount) == Decimal("-100.00")
    assert snap_upper_bound(Decimal("-100.001"), Invoice.amount) == Decimal("-100.01")


def test_snapping_never_raises_on_a_hostile_bound():
    """A filter must not 500 on an absurd query string. Such a bound is far
    outside the column's range, so the comparison is a no-op either way."""
    for bound in ("1E+400", "-1E+400", "1E-400"):
        assert isinstance(snap_lower_bound(Decimal(bound), Invoice.amount), Decimal)
        assert isinstance(snap_upper_bound(Decimal(bound), Invoice.amount), Decimal)


# ---------------------------------------------------------------------------
# Drift guard — shared predicates
# ---------------------------------------------------------------------------

#: A parameter whose name matches this is a candidate money bound. Deliberately
#: broad: a false positive costs one line in `_NOT_MONEY` with a reason, while a
#: miss silently readmits the defect this file exists to close.
_MONEY_NAME = re.compile(
    r"(amount|total|value|price|balance|budget|materiality|spend|cost|savings|fee|money|cash)",
    re.IGNORECASE,
)

#: Query parameters whose name matches `_MONEY_NAME` but which carry no money,
#: each with the reason. `test_every_exemption_is_still_a_real_parameter` keeps
#: these from decaying into stale excuses covering a future real bound.
_NOT_MONEY: dict[str, str] = {}


def _param_admits_exact_decimal(schema: dict) -> bool:
    """Can this OpenAPI parameter schema carry a value a double cannot hold?

    A `Decimal` parameter renders as `anyOf[number, string]` — FastAPI hands
    pydantic the raw query string, and the string branch is what parses
    exactly. A `float` renders as `number` alone, so every value it accepts has
    already been rounded to the nearest double before any application code runs.
    `integer` is exact by construction and passes.
    """
    branches = schema.get("anyOf") or schema.get("oneOf") or [schema]
    types = {b.get("type") for b in branches if isinstance(b, dict)}
    if "number" not in types:
        return True  # string / integer / boolean — nothing rounded on the way in
    return "string" in types


def _money_query_params(spec: dict) -> list[tuple[str, str, str, dict]]:
    """(method, path, name, schema) for every money-named query parameter."""
    found = []
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for param in operation.get("parameters", []) or []:
                if param.get("in") != "query":
                    continue
                if not _MONEY_NAME.search(param["name"]):
                    continue
                found.append((method.upper(), path, param["name"], param.get("schema", {})))
    return found


# ---------------------------------------------------------------------------
# Drift guard — the mounted OpenAPI schema (route parameters)
# ---------------------------------------------------------------------------


def test_discovery_finds_the_known_money_query_bounds():
    """Guards the guard: an empty discovery makes every check below vacuous."""
    params = _money_query_params(app.openapi())
    assert params, "discovery matched no money-named query parameters at all"
    surfaces = {(path, name) for _, path, name, _ in params}
    for expected in (
        ("/api/invoices", "amount_min"),
        ("/api/invoices", "amount_max"),
        ("/api/invoices/counts", "amount_min"),
        ("/api/invoices/ids", "amount_min"),
        ("/api/payments", "amount_min"),
        ("/api/payments/counts", "amount_min"),
        ("/api/vendor-statements/close-readiness", "materiality"),
        ("/api/budgets/check", "amount"),
    ):
        assert expected in surfaces, f"{expected} is no longer discovered as a money bound"


def test_no_money_query_bound_is_float_typed():
    """Every money bound on a mounted route must parse exactly.

    A `float`-typed bound is re-rounded to the nearest double before it reaches
    the `Numeric` column it is compared against, so a row sitting exactly on the
    boundary can fall the wrong side of the filter.
    """
    offenders = [
        f"{method} {path} ?{name}"
        for method, path, name, schema in _money_query_params(app.openapi())
        if name not in _NOT_MONEY and not _param_admits_exact_decimal(schema)
    ]
    assert not offenders, (
        "these money query bounds are float-typed and lose precision before "
        f"reaching an exact Numeric column: {offenders}. Declare the parameter "
        "`Decimal | None` (FastAPI parses the raw query string into it) — or, "
        "if the value is not money, add the name to `_NOT_MONEY` with a reason."
    )


def test_every_exemption_is_still_a_real_parameter():
    """An exemption for a parameter that no longer exists is a stale excuse
    that would silently cover a future money bound reusing the name."""
    live = {name for _, _, name, _ in _money_query_params(app.openapi())}
    for name, reason in _NOT_MONEY.items():
        assert name in live, f"{name} is exempted but no longer exists as a query parameter"
        assert reason.strip(), f"{name} is exempted with no reason"


def test_the_openapi_guard_rejects_a_float_bound():
    """Negative control — the predicate must actually fail something.

    Built by mounting a throwaway route rather than hand-writing a schema, so
    it stays honest if FastAPI/pydantic change how they render either type.
    """
    from fastapi import FastAPI

    probe = FastAPI()

    @probe.get("/probe")
    def _probe(  # pragma: no cover - never called, only introspected
        bad_amount_min: float | None = None,
        good_amount_min: Decimal | None = None,
    ):
        return {}

    schemas = {
        p["name"]: p["schema"] for p in probe.openapi()["paths"]["/probe"]["get"]["parameters"]
    }
    assert _param_admits_exact_decimal(schemas["good_amount_min"]) is True
    assert _param_admits_exact_decimal(schemas["bad_amount_min"]) is False
    # And discovery must reach both — a name pattern that misses them would make
    # the real scan pass while checking nothing.
    found = {name for _, _, name, _ in _money_query_params(probe.openapi())}
    assert found == {"bad_amount_min", "good_amount_min"}


# ---------------------------------------------------------------------------
# Drift guard — AST source scan (the shared private filter builders)
# ---------------------------------------------------------------------------

_API_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"


def _annotation_is_float(node: ast.expr | None) -> bool:
    """True for `float`, `float | None`, `Optional[float]` and friends."""
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id == "float"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_is_float(node.left) or _annotation_is_float(node.right)
    if isinstance(node, ast.Subscript):
        inner = node.slice
        if isinstance(inner, ast.Tuple):
            return any(_annotation_is_float(e) for e in inner.elts)
        return _annotation_is_float(inner)
    return False


def _float_money_params(source: str) -> list[str]:
    """`function:parameter` for every money-named `float` parameter in `source`.

    Covers ordinary functions too, not just routes: the shared
    `_invoice_list_filters` / `_payment_list_filters` builders are where the
    bound is actually compared against the column, and they appear in no
    OpenAPI schema at all.
    """
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if not _MONEY_NAME.search(arg.arg):
                continue
            if arg.arg in _NOT_MONEY:
                continue
            if _annotation_is_float(arg.annotation):
                offenders.append(f"{node.name}:{arg.arg}")
    return offenders


def test_no_money_filter_parameter_in_the_api_layer_is_float_annotated():
    offenders = []
    for path in sorted(_API_DIR.rglob("*.py")):
        for hit in _float_money_params(path.read_text()):
            offenders.append(f"{path.relative_to(_API_DIR.parents[1])}::{hit}")
    assert not offenders, (
        f"money-carrying parameters annotated `float`: {offenders}. Money is "
        "exact (root CLAUDE.md § Project invariants) — annotate `Decimal`. A "
        "`Decimal(str(value))` conversion inside the body does not fix it: the "
        "value was already rounded to the nearest double on the way in."
    )


def test_the_ast_scan_reaches_every_api_module():
    """An empty file list would make the scan above pass vacuously."""
    modules = list(_API_DIR.rglob("*.py"))
    assert len(modules) > 20, f"only {len(modules)} api modules scanned"
    assert (_API_DIR / "payments.py") in modules
    assert (_API_DIR / "invoices.py") in modules


def test_the_ast_scan_rejects_a_float_bound():
    """Negative control — the scan must actually fail an offending signature."""
    offending = (
        "from decimal import Decimal\n"
        "def _list_filters(query, *, amount_min: float | None, amount_max: float | None,\n"
        "                  status: str | None, page: int = 1):\n"
        "    if amount_min is not None:\n"
        "        query = query.where(Thing.amount >= Decimal(str(amount_min)))\n"
        "    return query\n"
    )
    assert sorted(_float_money_params(offending)) == [
        "_list_filters:amount_max",
        "_list_filters:amount_min",
    ]

    fixed = offending.replace("float | None", "Decimal | None").replace(
        "Decimal(str(amount_min))", "amount_min"
    )
    assert _float_money_params(fixed) == []

    # A non-money float parameter must NOT be flagged — a scan that fires on
    # everything gets exempted into uselessness.
    assert (
        _float_money_params("def f(confidence: float = 0.5, ratio: float | None = None): ...") == []
    )
