"""The Statements tab's free-text filter, on the server.

`GET /api/bank-reconciliation` offered only an EXACT ``account_identifier``
match, so the tab shipped with no search box at all: a free-text box over an
exact param returns nothing for a partial term, and a chip set built from page
one silently omits every account further down. Both are the "filter that
quietly hides rows" class `frontend/CLAUDE.md` § Search forbids, so the page
went out without one rather than with a lying one.

This suite pins the ``search`` leg that replaced that gap, and pins it against
the property that matters rather than the plumbing: **the rows, the ``total``
above them and the paging footer describe ONE set.** The list builds its row
query and its COUNT from the same `_statement_list_filters` object, which is
what makes that true; the tests page deliberately (``page_size=1``) because a
row that only shows up past page one is exactly what a client-side filter
cannot see.

The pure parse/match math is owned by ``test_bank_reconciliation.py``; the
router wiring by ``test_bank_reconciliation_api.py``.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

_TODAY = date.today()


def _csv(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode()


async def _upload(
    client,
    account_identifier: str,
    *,
    marker: str,
    period_start: date | None = None,
    period_end: date | None = None,
) -> str:
    """Import a one-line statement and return its id.

    ``marker`` only varies the CSV body so two statements on different accounts
    can't collide on the ``(org, account, sha256(body))`` import-idempotency
    index.
    """
    resp = await client.post(
        "/api/bank-reconciliation/upload",
        data={
            "account_identifier": account_identifier,
            "period_start": (period_start or _TODAY - timedelta(days=30)).isoformat(),
            "period_end": (period_end or _TODAY).isoformat(),
            "currency": "USD",
        },
        files={
            "file": (
                "statement.csv",
                _csv("Date,Amount,Description", f"{_TODAY.isoformat()},-11.00,{marker}"),
                "text/csv",
            )
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_search_matches_a_partial_account_and_the_count_narrows_with_it(realdb):
    """The defect in one test: a partial account term is what a reviewer types,
    and the ``total`` above the table must describe the narrowed set."""
    tag = uuid.uuid4().hex[:8]
    async with realdb.client(key="a", role="ap_manager") as c:
        operating = await _upload(c, f"Operating-{tag} ****1234", marker=f"op-{tag}")
        payroll = await _upload(c, f"Payroll-{tag} ****9876", marker=f"pr-{tag}")

        everything = (await c.get("/api/bank-reconciliation", params={"page_size": 100})).json()
        assert {operating, payroll} <= {i["id"] for i in everything["items"]}

        hit = (
            await c.get(
                "/api/bank-reconciliation",
                params={"search": f"operating-{tag}", "page_size": 100},
            )
        ).json()
        assert [i["id"] for i in hit["items"]] == [operating]
        # The count is built from the SAME filtered query as the rows, so it
        # can never head a narrowed table with a whole-set figure.
        assert hit["total"] == 1
        assert hit["total"] < everything["total"]

        # …and it narrows rather than replaces: the sibling is still findable.
        other = (
            await c.get("/api/bank-reconciliation", params={"search": f"payroll-{tag}"})
        ).json()
        assert [i["id"] for i in other["items"]] == [payroll]

        # A term matching nothing is an empty page under a zero count — not an
        # empty page under the whole-set total.
        none_found = (
            await c.get("/api/bank-reconciliation", params={"search": f"no-such-account-{tag}"})
        ).json()
        assert none_found["items"] == []
        assert none_found["total"] == 0


async def test_search_finds_a_statement_past_the_first_page(realdb):
    """The row a client-side filter could never see. Created FIRST, so the
    ``imported_at DESC`` order pushes it off page one."""
    tag = uuid.uuid4().hex[:8]
    async with realdb.client(key="a", role="ap_manager") as c:
        needle = await _upload(c, f"Zzz-Archive-{tag}", marker=f"needle-{tag}")
        for n in range(3):
            await _upload(c, f"Filler-{tag}-{n}", marker=f"filler-{tag}-{n}")

        page_one = (await c.get("/api/bank-reconciliation", params={"page_size": 1})).json()
        assert page_one["items"][0]["id"] != needle
        assert page_one["total"] > 1

        found = (
            await c.get(
                "/api/bank-reconciliation",
                params={"search": f"Zzz-Archive-{tag}", "page_size": 1},
            )
        ).json()
        assert [i["id"] for i in found["items"]] == [needle]
        assert found["total"] == 1


async def test_search_matches_source_format_and_the_iso_period(realdb):
    """The two non-account legs.

    ``source_format`` is the statement's own kind (the ``file_type`` leg
    ``/positive-pay`` carries). The period is matched in ISO form, NOT in the
    row's localised rendering — matching a localised label in SQL would make
    the result set depend on the caller's browser language.
    """
    tag = uuid.uuid4().hex[:8]
    # A period far enough back that no sibling test's default window overlaps it.
    start = date(2019, 4, 1)
    end = date(2019, 4, 30)
    async with realdb.client(key="a", role="ap_manager") as c:
        april = await _upload(
            c, f"Historic-{tag}", marker=f"apr-{tag}", period_start=start, period_end=end
        )

        by_period = (
            await c.get("/api/bank-reconciliation", params={"search": "2019-04", "page_size": 100})
        ).json()
        assert [i["id"] for i in by_period["items"]] == [april]
        assert by_period["total"] == 1

        # Every statement this router creates is `csv`, so the format leg can
        # only ever widen to the whole set — assert it MATCHES rather than that
        # it isolates, which is the honest claim.
        by_format = (
            await c.get("/api/bank-reconciliation", params={"search": "csv", "page_size": 100})
        ).json()
        assert april in {i["id"] for i in by_format["items"]}
        assert by_format["total"] == len(by_format["items"])


async def test_search_composes_with_the_exact_account_filter_and_can_only_narrow(realdb):
    """The pre-existing exact param still works, and the term intersects with
    it — a search can never re-widen past a filter already applied."""
    tag = uuid.uuid4().hex[:8]
    exact = f"Operating-{tag} ****1234"
    async with realdb.client(key="a", role="ap_manager") as c:
        operating = await _upload(c, exact, marker=f"op-{tag}")
        await _upload(c, f"Payroll-{tag} ****9876", marker=f"pr-{tag}")

        # Exact match alone — unchanged behaviour.
        only_exact = (
            await c.get("/api/bank-reconciliation", params={"account_identifier": exact})
        ).json()
        assert [i["id"] for i in only_exact["items"]] == [operating]
        assert only_exact["total"] == 1

        # Exact + a term that matches it: still that one row.
        both = (
            await c.get(
                "/api/bank-reconciliation",
                params={"account_identifier": exact, "search": "operating"},
            )
        ).json()
        assert [i["id"] for i in both["items"]] == [operating]
        assert both["total"] == 1

        # Exact + a term that does NOT match it: empty, never the sibling row.
        disjoint = (
            await c.get(
                "/api/bank-reconciliation",
                params={"account_identifier": exact, "search": f"payroll-{tag}"},
            )
        ).json()
        assert disjoint["items"] == []
        assert disjoint["total"] == 0


async def test_search_treats_like_metacharacters_as_text(realdb):
    """``_`` and ``%`` are LIKE syntax, not text — and a bank account label is
    exactly where they turn up (``Ops_2024``, ``Fee 50%``). Pinned through the
    shared `app.utils.search.ilike_contains` escaping."""
    tag = uuid.uuid4().hex[:8]
    async with realdb.client(key="a", role="ap_manager") as c:
        literal = await _upload(c, f"Ops_2024-{tag}", marker=f"lit-{tag}")
        decoy = await _upload(c, f"OpsX2024-{tag}", marker=f"dec-{tag}")

        hit = (
            await c.get(
                "/api/bank-reconciliation",
                params={"search": f"Ops_2024-{tag}", "page_size": 100},
            )
        ).json()
        assert [i["id"] for i in hit["items"]] == [literal]
        assert decoy not in {i["id"] for i in hit["items"]}
        assert hit["total"] == 1

        # A bare `%` must not match every row.
        wildcard = (await c.get("/api/bank-reconciliation", params={"search": "%"})).json()
        assert wildcard["total"] == 0


async def test_search_is_tenant_scoped(realdb):
    tag = uuid.uuid4().hex[:8]
    async with realdb.client(key="a", role="ap_manager") as c:
        await _upload(c, f"TenantA-Only-{tag}", marker=f"a-{tag}")

    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get("/api/bank-reconciliation", params={"search": f"TenantA-Only-{tag}"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
