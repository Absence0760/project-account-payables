"""Every paginated list orders by a TOTAL order, not just a timestamp.

`OFFSET`/`LIMIT` paging is only coherent if the `ORDER BY` is a total order.
`created_at` is not one: it defaults to the transaction timestamp, so every row
written by one transaction — an ERP sync page, a CSV import, a sweep tick —
shares it *exactly*. Postgres is then free to order tied rows differently
between the two queries that fetch page 1 and page 2, and a row can be handed
out twice or skipped entirely.

This is a **source** guard rather than a runtime one, deliberately. The bug is
planner-dependent: at fixture scale Postgres returns a stable scan order, so a
"page through tied rows and count them" test passes with and without the
tie-break and proves nothing. What is objectively checkable is the property the
correctness argument rests on — that the ordering is total — so that is what is
asserted, in the same spirit as the structural assertions in
`test_list_and_audit_indexes.py`.

Guarded shape: any `.order_by(<Model>.<ts>.desc()|asc())` whose only sort key is
a timestamp-ish column. The fix is always to append the primary key.
"""

from __future__ import annotations

import ast
import pathlib

API_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "api"

#: Columns that are NOT a total order on their own.
_TIMESTAMP_COLUMNS = frozenset(
    {
        "created_at",
        "updated_at",
        "txn_date",
        "checked_at",
        "issued_at",
        "occurred_at",
        "scheduled_date",
        "statement_date",
    }
)

#: Sort keys that ARE unique per row, so a single-key order is already total.
_TOTAL_ON_THEIR_OWN = frozenset({"id"})

#: Written exemptions only. A bare `ORDER BY <timestamp>` is acceptable where the
#: query cannot paginate — an aggregate, a `LIMIT 1`, a whole-set fetch. Each
#: entry names the callsite and why paging can never split it.
_EXEMPT: dict[tuple[str, int], str] = {}


def _unwrap(node: ast.AST) -> ast.AST:
    """Strip `.desc()` / `.asc()` / `.nulls_last()` off a sort key."""
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr not in {"desc", "asc", "nulls_last", "nulls_first"}:
            break
        node = node.func.value
    return node


def _sort_keys(call: ast.Call) -> list[str] | None:
    """Return the attribute names in an `.order_by(...)` call, or None."""
    keys: list[str] = []
    for arg in call.args:
        node = _unwrap(arg)
        if isinstance(node, ast.Attribute):
            keys.append(node.attr)
        else:
            # a literal, an f-string, a resolved variable — can't judge it
            return None
    return keys or None


def _order_by_in_chain(node: ast.AST) -> ast.Call | None:
    """Find the `.order_by(...)` call in the same method chain as `node`.

    Only a chained ordering can be attributed to this paging call with
    confidence. An `order_by` applied to the query on an earlier line is not
    reached, which keeps the guard silent rather than wrong.
    """
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "order_by":
            return node
        node = node.func.value
    return None


def test_paginated_lists_order_by_a_total_order():
    """Only queries that actually PAGE are judged.

    A bare `ORDER BY created_at` is perfectly fine on a `LIMIT 1` lookup, an
    aggregate, or a whole-set fetch — nothing splits the tied rows across two
    requests there. The defect is specifically OFFSET paging over a non-total
    order, so the guard keys on the `.offset(...)` call and looks for the
    ordering in that same chain.
    """
    offenders: list[str] = []
    for path in sorted(API_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "offset"
            ):
                continue
            order_by = _order_by_in_chain(node.func.value)
            if order_by is None:
                continue
            keys = _sort_keys(order_by)
            if not keys:
                continue
            if any(k in _TOTAL_ON_THEIR_OWN for k in keys):
                continue
            if not any(k in _TIMESTAMP_COLUMNS for k in keys):
                continue
            rel = path.relative_to(API_DIR.parent.parent)
            if (str(rel), order_by.lineno) in _EXEMPT:
                continue
            offenders.append(f"{rel}:{order_by.lineno} order_by({', '.join(keys)})")

    assert not offenders, (
        "these paginated queries sort on a timestamp with no unique tie-break, "
        "so OFFSET paging over them can duplicate or drop a row when two rows "
        "share the timestamp (one transaction => one `now()`). Append the "
        "primary key to the ORDER BY:\n  " + "\n  ".join(offenders)
    )
