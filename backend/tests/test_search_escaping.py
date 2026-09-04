r"""A search term is text the user typed, not a LIKE pattern.

Every free-text list filter in this backend is an ``ILIKE '%term%'``. That was
always parameterised — this is not an injection — but ``%``, ``_`` and ``\`` are
LIKE *metacharacters*, and interpolating the raw term into the pattern hands
them to the engine as syntax:

* ``%`` matches any run of characters, so searching ``50%`` returned **every**
  row. On `/expenses` and `/requisitions` that is worse than a wrong list: the
  whole-set KPI rollup above the table shares the list's filter builder, so the
  count and the per-currency totals confidently describe a set the user never
  asked for.
* ``_`` matches any single character, so ``INV_001`` also matched ``INV-001``
  and ``INV.001`` — three distinct invoices reported as one search hit.
* a trailing ``\`` escapes the closing ``%``.

None of those are exotic spellings. Invoice and PO numbers carry ``_``, an
expense description carries ``%`` whenever a discount or tax rate is written
out, and a GL account code carries both.

`api/portal.py` already got this right for the supplier portal's invoice search
— the one place a literal ``_`` in a vendor's own invoice number was certain to
turn up. `app/utils/search.py` is that fix with one owner; this file pins the
helper, pins the behaviour end-to-end on the list surfaces the round-13 search
work landed on, and guards against the eighteenth call site quietly rebuilding
the pattern by hand.
"""

from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

from app.utils.search import (
    LIKE_ESCAPE_CHAR,
    escape_like,
    ilike_contains,
    like_contains_pattern,
)

pytestmark = pytest.mark.asyncio

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

#: `.like()` / `.ilike()` calls that legitimately take an f-string because the
#: interpolated value is NOT user input and the match is not "contains".
#: Anything else belongs on `ilike_contains`.
FSTRING_LIKE_ALLOWLIST = {
    # A suffix match (`%suffix`, deliberately not `%suffix%`) over the module's
    # own frozen `_READ_ACTION_SUFFIXES` vocabulary — no request data reaches it.
    "services/access_review.py",
    # A prefix match (`INTK-<year>-%`) over a prefix this module builds itself
    # from the current year — again, no request data.
    "services/intake_service.py",
}


# ---------------------------------------------------------------------------
# The pure helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("plain", "plain"),
        ("50%", r"50\%"),
        ("%", r"\%"),
        ("INV_001", r"INV\_001"),
        ("a_b%c", r"a\_b\%c"),
        # The backslash is doubled FIRST. Doing it last would re-escape the
        # escapes just inserted and hand `%` back to the engine as a wildcard.
        ("back\\slash", "back\\\\slash"),
        ("100%\\", "100\\%\\\\"),
        ("", ""),
    ],
)
def test_escape_like_neutralises_every_metacharacter(term, expected):
    assert escape_like(term) == expected


def test_escaping_is_not_idempotent_and_must_be_applied_exactly_once():
    r"""`escape_like(escape_like(x))` is deliberately NOT `escape_like(x)`.

    Escaping an already-escaped term turns the escape character itself into
    literal text (`\%` becomes `\\\%` — a literal backslash followed by a
    literal percent), so the term stops matching the row it came from. Pinned so
    nobody "hardens" a call site by escaping again on the way in: the single
    application belongs in `ilike_contains`, and callers pass raw user text.
    """
    once = escape_like("50%")
    assert escape_like(once) != once


def test_like_contains_pattern_wraps_the_escaped_term():
    assert like_contains_pattern("50%") == r"%50\%%"
    assert like_contains_pattern("x") == "%x%"


def test_ilike_contains_declares_the_escape_character_it_used():
    r"""Both halves have to agree: the pattern doubles ``\`` and the SQL clause
    declares ``ESCAPE '\'``. A clause that escapes the pattern but forgets the
    argument matches literal backslashes instead of neutralising the wildcard,
    which is a *different* wrong answer — which is exactly why the two live in
    one function."""
    from app.models.expense import Expense

    compiled = str(ilike_contains(Expense.merchant, "50%").compile())
    assert "ESCAPE" in compiled.upper()
    assert LIKE_ESCAPE_CHAR == "\\"


# ---------------------------------------------------------------------------
# End-to-end: the list surfaces the round-13 search work landed on
# ---------------------------------------------------------------------------


async def test_percent_in_an_expense_search_is_literal_not_a_wildcard(realdb):
    """The headline case. `%` alone used to match every row in the tenant, and
    the KPI rollup above the table agreed with it — so the page reported a
    filtered view of everything."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        hit = (
            await c.post(
                "/api/expenses",
                json={
                    "expense_date": "2026-06-01",
                    "merchant": "Acme Freight",
                    "description": "Fuel surcharge 50% of base",
                    "amount": "120.00",
                },
            )
        ).json()["id"]
        await c.post(
            "/api/expenses",
            json={"expense_date": "2026-06-02", "merchant": "Unrelated Diner", "amount": "18.00"},
        )
        await c.post(
            "/api/expenses",
            json={"expense_date": "2026-06-03", "merchant": "Also Unrelated", "amount": "19.00"},
        )

        matched = (await c.get("/api/expenses", params={"search": "50%"})).json()
        assert matched["total"] == 1
        assert matched["items"][0]["id"] == hit

        # A bare `%` means "rows containing a percent sign" — here, exactly the
        # one — not "every row".
        everything = (await c.get("/api/expenses", params={"search": "%"})).json()
        assert everything["total"] == 1
        assert everything["items"][0]["id"] == hit

        # …and the rollup that sits above the table says the same, because it
        # shares the filter builder.
        rolled = (await c.get("/api/expenses/summary", params={"search": "%"})).json()
        assert rolled["total"] == 1

        # …as does the "select all N matching" resolver.
        ids = (await c.get("/api/expenses/ids", params={"search": "%"})).json()
        assert ids["total"] == 1
        assert ids["ids"] == [hit]


async def test_underscore_in_an_expense_search_matches_only_the_literal(realdb):
    """`_` is LIKE's single-character wildcard, so `AB_1` used to also match
    `AB-1` and `AB.1` — different merchants folded into one result."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        literal = (
            await c.post(
                "/api/expenses",
                json={"expense_date": "2026-06-01", "merchant": "COST_CTR", "amount": "10.00"},
            )
        ).json()["id"]
        await c.post(
            "/api/expenses",
            json={"expense_date": "2026-06-02", "merchant": "COST-CTR", "amount": "11.00"},
        )

        found = (await c.get("/api/expenses", params={"search": "COST_CTR"})).json()
        assert found["total"] == 1
        assert found["items"][0]["id"] == literal


async def test_backslash_in_a_search_term_neither_matches_wide_nor_errors(realdb):
    r"""A trailing ``\`` escapes the pattern's own closing ``%``. Unescaped that
    is at best a wrong result and at worst a driver-level error on the list
    endpoint — either way a term a user can type must not be able to produce
    it."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        await c.post(
            "/api/expenses",
            json={"expense_date": "2026-06-01", "merchant": "Backslash Co", "amount": "10.00"},
        )

        resp = await c.get("/api/expenses", params={"search": "path\\"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        resp = await c.get("/api/expenses", params={"search": "\\%"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


async def test_percent_in_a_requisition_search_is_literal(realdb):
    """Same defect, the other page the round-13 search work landed on — and the
    one whose search covers `department`, a column people abbreviate with
    punctuation."""
    async with realdb.client(key="a", role="ap_manager") as c:
        created = await c.post(
            "/api/requisitions",
            json={
                "requisition_number": f"REQ-PCT-{uuid.uuid4().hex[:8]}",
                "title": "Freight uplift 50% Q3",
                "department": "Logistics",
                "line_items": [{"description": "Uplift", "quantity": "1", "unit_price": "100.00"}],
            },
        )
        assert created.status_code == 201, created.text
        req_id = created.json()["id"]
        await c.post(
            "/api/requisitions",
            json={
                "requisition_number": f"REQ-PCT-{uuid.uuid4().hex[:8]}",
                "title": "Unrelated desk chairs",
                "department": "Facilities",
                "line_items": [{"description": "Chair", "quantity": "1", "unit_price": "50.00"}],
            },
        )

        found = (await c.get("/api/requisitions", params={"search": "50%"})).json()
        assert found["total"] == 1
        assert found["items"][0]["id"] == req_id

        # A bare `%` finds the one title that literally contains a percent
        # sign, not both requisitions.
        wildcard = (await c.get("/api/requisitions", params={"search": "%"})).json()
        assert wildcard["total"] == 1
        assert wildcard["items"][0]["id"] == req_id
        # The KPI rollup shares `_requisition_list_filters`, so it agrees.
        assert (await c.get("/api/requisitions/summary", params={"search": "%"})).json()[
            "total"
        ] == 1


async def test_percent_in_a_vendor_search_is_literal(realdb):
    """`/vendors` predates the round-13 work and had the same unescaped
    pattern; its `GET /counts` badge shares the predicate, so a wildcard term
    inflated the attention badge too."""
    async with realdb.client(key="a", role="ap_manager") as c:
        created = await c.post("/api/vendors", json={"name": "Discount 10% Supplies"})
        assert created.status_code in (200, 201)
        await c.post("/api/vendors", json={"name": "Ordinary Supplies"})

        found = (await c.get("/api/vendors", params={"search": "10%"})).json()
        assert found["total"] == 1
        assert found["items"][0]["name"] == "Discount 10% Supplies"

        wildcard = (await c.get("/api/vendors", params={"search": "%"})).json()
        assert wildcard["total"] == 1
        assert wildcard["items"][0]["name"] == "Discount 10% Supplies"
        counts = (await c.get("/api/vendors/counts", params={"search": "%"})).json()
        assert counts["total"] == 1


# ---------------------------------------------------------------------------
# Drift guard
# ---------------------------------------------------------------------------


def _fstring_like_call_lines(source: str, *, filename: str) -> list[int]:
    """Line numbers of every ``.like(f"…")`` / ``.ilike(f"…")`` in ``source``.

    An f-string argument is the signature of a hand-built pattern: the term is
    being interpolated at the call site, which is precisely where the escaping
    gets forgotten. A pre-escaped `str` variable (what `ilike_contains` passes)
    is an `ast.Name` and never flags.
    """
    tree = ast.parse(source, filename=filename)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"like", "ilike"}
        and node.args
        and isinstance(node.args[0], ast.JoinedStr)
    ]


def test_no_module_hand_builds_a_like_pattern():
    """Source scan: LIKE patterns are built in one place.

    Eighteen call sites spelled `f"%{search}%"` inline, each of them one edit
    away from being the one that forgets `escape=`. `ilike_contains` cannot
    forget it — the pattern and the escape argument are produced together — so
    the guard is simply that nobody interpolates a pattern at a call site.
    """
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        relative = str(path.relative_to(APP_DIR))
        if relative in FSTRING_LIKE_ALLOWLIST:
            continue
        lines = _fstring_like_call_lines(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(f"{relative}:{line}" for line in lines)

    assert not offenders, (
        f"hand-built LIKE pattern(s) at {offenders}. Use "
        "`app.utils.search.ilike_contains(column, term)` — an interpolated "
        '`f"%{term}%"` hands the user\'s `%` and `_` to the engine as '
        "wildcards, which widens the result set (and the whole-set KPI rollup "
        "built on the same filter) beyond what was asked for."
    )
