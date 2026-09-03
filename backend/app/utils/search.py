r"""One definition of "the user typed a substring to look for".

Every free-text list filter in this backend is a SQL ``ILIKE`` over a
``%term%`` pattern. That is parameterised, so it was never an injection — but
``%``, ``_`` and ``\`` are LIKE *metacharacters*, and interpolating a raw term
into the pattern hands them to the engine as syntax instead of as text:

* ``%`` matches any run of characters, so searching ``50%`` on the expenses
  list returns every row rather than the ones whose merchant or description
  actually contains ``50%`` — a **wider** set than the one the page claims to
  be showing, which on a whole-set KPI rollup means the count above the table
  is answering a different question than the user asked;
* ``_`` matches any single character, so ``INV_001`` also matches ``INV-001``
  and ``INV.001`` — three different invoices reported as one search hit;
* a trailing ``\`` escapes the closing ``%`` and, on some backends, errors.

None of those are hypothetical spellings. Invoice and PO numbers routinely
carry ``_``, an expense description carries ``%`` whenever a discount or a tax
rate is written out, and a GL account code carries both.

``api/portal.py`` already got this right for the supplier portal's invoice
search (a vendor's own number is the one place a literal ``_`` was certain to
show up). This module is that fix with one owner and no per-call-site copy —
the same shape ``app/utils/emails.py`` gives the email check, and for the same
reason: a rule spelled once at eighteen call sites drifts.

Escaping is done in Python rather than by asking Postgres for
``ESCAPE '\'``-flavoured input, because the pattern is what we build; the
``escape=`` argument on the SQLAlchemy side only tells the engine which
character we chose. Both halves have to agree, which is exactly why they
belong in one function.
"""

from __future__ import annotations

__all__ = ["LIKE_ESCAPE_CHAR", "escape_like", "ilike_contains", "like_contains_pattern"]

#: The character `escape_like` doubles and that every `ilike(...)` built here
#: declares. Backslash matches the SQL standard default and what
#: `api/portal.py` used before this module existed.
LIKE_ESCAPE_CHAR = "\\"


def escape_like(term: str) -> str:
    r"""Neutralise LIKE metacharacters in ``term``.

    ``\`` first — doubling it after the others would re-escape the escapes we
    just inserted and turn ``%`` back into a wildcard.
    """
    return (
        term.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
        .replace("%", LIKE_ESCAPE_CHAR + "%")
        .replace("_", LIKE_ESCAPE_CHAR + "_")
    )


def like_contains_pattern(term: str) -> str:
    """``term`` as a literal-substring LIKE pattern (``%escaped%``).

    For the handful of call sites that need the pattern itself — a raw
    ``text()`` query, or a filter built somewhere the column isn't in scope.
    Anything holding a column should use :func:`ilike_contains`, which cannot
    forget the matching ``escape=``.
    """
    return f"%{escape_like(term)}%"


def ilike_contains(column, term: str):
    """A case-insensitive "contains this literal text" clause on ``column``.

    The term is treated as text, not as a pattern: a user searching ``50%``
    gets the rows containing ``50%``. Callers decide emptiness — an empty term
    here yields ``column ILIKE '%%'``, which matches every non-NULL row, so
    guard with ``if term.strip():`` before building the clause (every list
    filter in ``app/api`` already does).
    """
    return column.ilike(like_contains_pattern(term), escape=LIKE_ESCAPE_CHAR)
