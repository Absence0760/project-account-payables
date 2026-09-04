"""Exact money bounds for list-endpoint filters.

Two separate things have to go right for a money filter bound to select the
right rows, and the obvious half is not the load-bearing one.

**Type.** A bound declared ``float`` is rounded to the nearest double before any
application code sees it, so a later ``Decimal(str(value))`` recovers the
double, not what the caller sent. Declaring the parameter ``Decimal | None``
fixes that outright — FastAPI hands pydantic the raw query string, which parses
exactly (root ``CLAUDE.md`` § Project invariants: money is exact).

**Scale.** Typing alone is still not enough. SQLAlchemy types a comparison's
bind parameter from the column, and the asyncpg dialect renders that as a bind
cast — ``invoices.amount >= $1::NUMERIC(15, 2)`` — so Postgres rounds an
over-precise bound to the column's own scale before comparing. A lower bound of
``100.00000000000000001`` becomes ``100.00`` and admits the very boundary row it
was written to exclude; an upper bound of ``99.99999999999999999`` does the same
in the other direction. Rounding a bound to nearest is never right, because the
direction of the comparison decides which way is safe.

Money columns are a fixed grid (``Numeric(15, 2)`` — every stored amount is a
whole number of cents), so the exact answer is to snap the bound onto that grid
in the direction that preserves the set: a lower bound rounds UP (the smallest
representable amount that still satisfies ``>=``), an upper bound rounds DOWN.
That is exact for a bound of any precision, and it derives the scale from the
column itself rather than restating ``2`` at each call site.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation, localcontext

from sqlalchemy import Numeric

__all__ = ["snap_lower_bound", "snap_upper_bound"]

# Wide enough that quantizing any plausible bound cannot overflow the context;
# an absurd one falls back to the caller's value rather than raising on a read.
_SNAP_PRECISION = 60


def _column_scale(column) -> int | None:
    """Decimal places the column stores, or ``None`` if it isn't a fixed grid."""
    column_type = getattr(column, "type", None)
    if not isinstance(column_type, Numeric):
        return None
    scale = column_type.scale
    return scale if isinstance(scale, int) and scale >= 0 else None


def _snap(value: Decimal, column, rounding: str) -> Decimal:
    scale = _column_scale(column)
    if scale is None:
        return value
    try:
        with localcontext() as ctx:
            ctx.prec = _SNAP_PRECISION
            return value.quantize(Decimal(1).scaleb(-scale), rounding=rounding)
    except (InvalidOperation, OverflowError, ValueError):
        # Far outside the column's range — the comparison is a no-op either way,
        # and a filter must never 500 on a hostile bound.
        return value


def snap_lower_bound(value: Decimal, column) -> Decimal:
    """The smallest value ``column`` can hold that still satisfies ``>= value``.

    Rounds UP, so a bound between two representable amounts excludes the lower
    one instead of being rounded back down onto it.
    """
    return _snap(value, column, ROUND_CEILING)


def snap_upper_bound(value: Decimal, column) -> Decimal:
    """The largest value ``column`` can hold that still satisfies ``<= value``.

    Rounds DOWN, the mirror of :func:`snap_lower_bound`.
    """
    return _snap(value, column, ROUND_FLOOR)
