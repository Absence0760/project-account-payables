"""The expense CSV is the rows on screen — one filter builder, both surfaces.

`GET /api/expenses/export` restated the list's `status` / `report_id` clauses
inline and declared no `search` leg at all. Two consequences, only one of which
was visible:

* **The CSV silently ignored the search.** FastAPI drops an undeclared query
  param without complaint, so a term sent to `/export` was a no-op rather than
  a filter — an operator searching "Skyline", clicking Export CSV and getting
  the whole status-filtered register back would have no way to tell the file
  disagreed with the table. The frontend refused to pretend (it kept a separate
  `buildExportParams()` that withheld the term), which made the asymmetry
  honest but did not remove it: the two surfaces still disagreed about what
  "filtered" meant.
* **The restated clauses were free to drift.** `_expense_list_filters` exists
  precisely because the list and its whole-set KPI rollup had already drifted
  once; a third copy of the same predicates in the export is the same trap one
  file lower.

Both are closed by routing the export through `_expense_list_filters`. The
export-only filters (`category`, the date range) stay inline on purpose: they
are not on the list surface, and slicing a period is what the CSV is for.

These tests assert the property, not the plumbing — every case compares the
CSV's own data rows against what `GET /api/expenses` returns for the same
params, so an export that stops honouring a filter fails here even if it is
re-implemented some other way.
"""

from __future__ import annotations

import csv
import io

import pytest

pytestmark = pytest.mark.asyncio


def _csv_rows(body: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body)))


def _merchants(body: str) -> set[str]:
    return {row["merchant"] for row in _csv_rows(body)}


async def _seed(client) -> dict[str, str]:
    """Three expenses whose merchant / description / category each isolate one
    of the columns the list search covers."""
    ids: dict[str, str] = {}
    for merchant, category, description, amount in (
        ("Skyline Hotels", "lodging", "Two nights, client visit", "310.00"),
        ("Uber", "travel", "Airport transfer", "42.50"),
        ("Corner Deli", "meals", "Team lunch", "88.25"),
    ):
        resp = await client.post(
            "/api/expenses",
            json={
                "expense_date": "2026-06-01",
                "merchant": merchant,
                "category": category,
                "description": description,
                "amount": amount,
            },
        )
        assert resp.status_code == 201, resp.text
        ids[merchant] = resp.json()["id"]
    return ids


async def test_export_honours_the_search_term(realdb):
    """The headline: a CSV taken during a search contains the searched rows and
    only those."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        await _seed(c)

        resp = await c.get("/api/expenses/export", params={"search": "Skyline"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert _merchants(resp.text) == {"Skyline Hotels"}


async def test_export_search_covers_the_same_columns_as_the_list(realdb):
    """Merchant, description and category — the three the list ILIKEs. A CSV
    that searched fewer columns would be a *different* filter wearing the same
    search box."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        await _seed(c)

        by_description = await c.get("/api/expenses/export", params={"search": "client visit"})
        assert _merchants(by_description.text) == {"Skyline Hotels"}

        by_category = await c.get("/api/expenses/export", params={"search": "TRAVEL"})
        assert _merchants(by_category.text) == {"Uber"}


async def test_export_and_list_agree_across_every_filter_combination(realdb):
    """The property that matters: for any filter set the page can produce, the
    CSV holds exactly the rows the table does.

    Compares the two responses rather than a hardcoded expectation, so this
    keeps holding if the seed, the columns or the ordering change — and fails
    the moment either surface grows a predicate the other lacks."""
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        ids = await _seed(c)

        # One row into a non-draft status so `status` is a real discriminator.
        import uuid as _uuid

        from sqlalchemy import select

        from app.models.expense import Expense

        async with mk() as s:
            row = (
                await s.execute(
                    select(Expense).where(Expense.id == _uuid.UUID(ids["Skyline Hotels"]))
                )
            ).scalar_one()
            row.status = "submitted"
            await s.commit()

        for params in (
            {},
            {"search": "skyline"},
            {"status": "submitted"},
            {"status": "draft"},
            {"search": "skyline", "status": "submitted"},
            # The pairing that used to be silently empty of meaning: a term
            # that matches a row the status filter excludes.
            {"search": "skyline", "status": "draft"},
            {"search": "zzz-nothing-matches"},
        ):
            listed = (await c.get("/api/expenses", params={**params, "page_size": 100})).json()
            exported = await c.get("/api/expenses/export", params=params)
            assert exported.status_code == 200, params
            assert _merchants(exported.text) == {item["merchant"] for item in listed["items"]}, (
                params
            )
            assert len(_csv_rows(exported.text)) == listed["total"], params


async def test_export_search_is_literal_not_a_wildcard(realdb):
    """The export shares the list's filter builder, so it inherits the
    LIKE-metacharacter escaping too — a `%` in the term is text, not "every
    row". Asserted here rather than only on the list because the export is the
    surface where a silently-widened set leaves the building as a file."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        await _seed(c)
        await c.post(
            "/api/expenses",
            json={
                "expense_date": "2026-06-02",
                "merchant": "Acme Freight",
                "description": "Fuel surcharge 50% of base",
                "amount": "120.00",
            },
        )

        resp = await c.get("/api/expenses/export", params={"search": "%"})
        assert _merchants(resp.text) == {"Acme Freight"}


async def test_export_still_honours_its_own_export_only_filters(realdb):
    """`category` and the date range are deliberately NOT on the list surface;
    routing status/report/search through the shared builder must not drop
    them."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        await _seed(c)
        await c.post(
            "/api/expenses",
            json={
                "expense_date": "2026-01-15",
                "merchant": "Old Vendor",
                "category": "travel",
                "amount": "10.00",
            },
        )

        by_category = await c.get("/api/expenses/export", params={"category": "lodging"})
        assert _merchants(by_category.text) == {"Skyline Hotels"}

        windowed = await c.get(
            "/api/expenses/export", params={"date_from": "2026-05-01", "date_to": "2026-06-30"}
        )
        assert "Old Vendor" not in _merchants(windowed.text)
        assert "Skyline Hotels" in _merchants(windowed.text)

        # …and they compose with the search rather than replacing it.
        both = await c.get(
            "/api/expenses/export", params={"category": "travel", "search": "airport"}
        )
        assert _merchants(both.text) == {"Uber"}


async def test_export_is_tenant_scoped(realdb):
    """A filter change is exactly the kind of edit that can widen a scope by
    accident, so the isolation assertion rides along with it."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        await _seed(c)

    async with realdb.client(key="b", role="ap_clerk") as c:
        resp = await c.get("/api/expenses/export", params={"search": "skyline"})
    assert resp.status_code == 200
    assert _csv_rows(resp.text) == []


# Presentation, not filtering: a CSV has no page and carries its own order.
_VIEW_ONLY_PARAMS = {"sort", "order", "page", "page_size"}


def _openapi_query_params(path: str) -> set[str]:
    """Query-parameter names FastAPI actually resolves for `GET path`.

    Read off the MOUNTED schema rather than `inspect.signature`, because a
    scalar parameter declared with a plain default (`search: str | None = None`)
    is a real query parameter to FastAPI but is NOT a `fastapi.params.Query`
    instance. A signature-level check keyed on that isinstance passes
    *vacuously* the moment a parameter is written that way — the precise defect
    that let the previous whole-set-KPI guard sit green while checking nothing
    on `/api/recurring` (PR #356). Same resolution `tests/
    test_whole_set_kpi_rollups.py` uses, for the same reason.
    """
    from app.main import app

    operation = app.openapi()["paths"][path]["get"]
    return {p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"}


async def test_export_declares_every_filter_the_list_offers(realdb):
    """A contract-level guard, cheap and total: whatever a user can narrow the
    list by, the CSV button must be able to send. This is what would have
    caught the missing `search` leg the day it diverged — the behavioural tests
    above only cover the filters someone thought to write a case for."""
    list_params = _openapi_query_params("/api/expenses") - _VIEW_ONLY_PARAMS
    export_params = _openapi_query_params("/api/expenses/export")

    # Guards the guard: if parameter resolution ever stops seeing the list's
    # filters, `list_only` empties and the assertion below becomes a no-op.
    assert {"status", "search", "report_id"} <= list_params, sorted(list_params)

    list_only = sorted(list_params - export_params)
    assert not list_only, (
        f"GET /api/expenses/export cannot express the list filter(s) {list_only}. "
        "FastAPI drops an undeclared query param silently, so the CSV would "
        "quietly cover a wider set than the table it was exported from."
    )


async def test_export_only_filters_are_not_on_the_list_surface(realdb):
    """The other direction, recorded so it stays deliberate: `category` and the
    date range are export-only. If one of them ever lands on the list too, it
    has to move into the shared `_expense_list_filters` — otherwise the CSV and
    the table would each apply their own copy of the same predicate, which is
    the drift the shared builder exists to prevent."""
    list_params = _openapi_query_params("/api/expenses")
    export_only = _openapi_query_params("/api/expenses/export") - list_params
    assert export_only == {"category", "date_from", "date_to"}, sorted(export_only)
