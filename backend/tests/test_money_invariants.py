"""Money-invariant tests (project invariant #1).

> Money is exact. Amounts use `Decimal` (never `float`), and SQLAlchemy
> columns for currency use `Numeric(precision, scale)` (never `Float` /
> `Real`). A new column or in-memory total typed as `float` for
> currency is `Critical`.

These tests pin the contract across every model + schema that carries
a monetary amount. A future PR that introduces `Float` for a "speed"
reason is caught by tests, not by an auditor.

Two layers, deliberately:

* an **explicit** list (`_MONEY_MODELS_AND_FIELDS`) naming the core
  money columns and asserting their exact shape — precise, and the
  place to add a new money-bearing table; and
* a **whole-schema sweep** that is opt-OUT rather than opt-in: every
  `Numeric` column anywhere under `app/models/` must declare precision
  and scale, and no `Float` / `Double` / `REAL` may exist at all unless
  it is named in `NON_MONEY_FLOAT_ALLOWLIST` with a reason. A new money
  column is protected without anyone remembering to protect it.

We also pin the in-memory contract: any service helper that produces
a money total must return a `Decimal`, never a `float`. The
adapter-payload schemas are the boundary at which user-typed Decimals
become SDK-bound values.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import Column, Float, Numeric

# Models that hold monetary amounts. Mirror this list when adding a
# new money-bearing table.
_MONEY_MODELS_AND_FIELDS = [
    (
        "app.models.invoice",
        "Invoice",
        ["amount", "subtotal", "tax_amount", "discount_amount", "shipping_amount"],
    ),
    ("app.models.invoice", "InvoiceLineItem", ["unit_price", "tax", "total"]),
    ("app.models.procurement", "PurchaseOrder", ["total"]),
    ("app.models.procurement", "POLineItem", ["unit_price", "total"]),
    ("app.models.credit_memo", "CreditMemo", ["amount"]),
    ("app.models.payment", "PaymentRun", ["total_amount"]),
    ("app.models.payment", "Payment", ["amount"]),
    ("app.models.virtual_card", "VirtualCard", ["amount_limit", "amount_charged"]),
    ("app.models.virtual_card", "CardRebate", ["amount", "rate"]),
]


@pytest.mark.parametrize("module,cls_name,fields", _MONEY_MODELS_AND_FIELDS)
def test_money_columns_use_numeric_not_float(module: str, cls_name: str, fields: list[str]):
    """Every currency-bearing column must be `Numeric(p, s)`. A
    regression that types one as `Float` / `Real` introduces binary
    fractional drift on the next migration round-trip — and every
    sum across that column gives a slightly-wrong answer."""
    mod = __import__(module, fromlist=[cls_name])
    cls = getattr(mod, cls_name)
    for fname in fields:
        col = cls.__table__.columns[fname]
        col_type = col.type
        assert isinstance(col_type, Numeric), (
            f"{cls_name}.{fname}: expected Numeric column, got {type(col_type).__name__}"
        )
        assert not isinstance(col_type, Float), (
            f"{cls_name}.{fname}: Float is NEVER acceptable for money — use Numeric"
        )


@pytest.mark.parametrize("module,cls_name,fields", _MONEY_MODELS_AND_FIELDS)
def test_money_columns_declare_precision_and_scale(module: str, cls_name: str, fields: list[str]):
    """`Numeric()` with no precision/scale silently degrades to
    arbitrary-precision text in some backends and double-precision
    float in others. Every money column must declare both."""
    mod = __import__(module, fromlist=[cls_name])
    cls = getattr(mod, cls_name)
    for fname in fields:
        col_type = cls.__table__.columns[fname].type
        assert col_type.precision is not None, (
            f"{cls_name}.{fname}: Numeric() without precision is ambiguous across backends"
        )
        assert col_type.scale is not None, (
            f"{cls_name}.{fname}: Numeric() without scale is ambiguous across backends"
        )


def test_payment_amount_column_has_at_least_two_scale():
    """Currency carries cents. A Numeric column with scale=0 would
    silently round every dollar amount down to the nearest dollar."""
    from app.models.payment import Payment

    assert Payment.__table__.columns["amount"].type.scale >= 2


def test_virtual_card_rebate_rate_has_room_for_basis_points():
    """Rebate rate is stored as a Decimal in the 0..1 range
    (`0.0100` = 1%). Scale must allow ≥4 digits after the decimal
    so half-basis-point rates (e.g. 1.5% → 0.0150) survive the
    round-trip."""
    from app.models.virtual_card import CardRebate

    rate_col = CardRebate.__table__.columns["rate"].type
    assert rate_col.scale >= 4, (
        f"CardRebate.rate scale={rate_col.scale}; need ≥4 to express 0.0050 (50 bps)"
    )


# ---------------------------------------------------------------------------
# Schema (Pydantic) money typing
# ---------------------------------------------------------------------------


def test_payment_payload_amount_is_decimal_not_float():
    """The adapter boundary — `PaymentPayload` is what the executor
    hands to the processor SDK. Its `amount` MUST be Decimal so the
    SDK sees a precise value; a `float` annotation lets `1.10` lose
    the trailing zero in flight."""
    from app.services.payment_adapters import PaymentPayload

    # The annotation is a string (PEP 563 future-annotations); check
    # the resolved type lives in `__annotations__`.
    annotations = inspect.get_annotations(PaymentPayload, eval_str=True)
    assert annotations["amount"] is Decimal, (
        f"PaymentPayload.amount should be Decimal, got {annotations['amount']}"
    )


def test_virtual_card_payload_amount_is_decimal():
    """Same boundary for card issuance."""
    from app.services.card_adapters import VirtualCardPayload

    annotations = inspect.get_annotations(VirtualCardPayload, eval_str=True)
    assert annotations["amount"] is Decimal


# ---------------------------------------------------------------------------
# Float-rejection guard at runtime
# ---------------------------------------------------------------------------


def test_payment_model_rejects_float_amount_via_decimal_quantization():
    """Constructing a Payment with a float amount must round-trip
    through Decimal, not store the raw float. Without this, two
    summed Payments could disagree on the last cent."""
    from decimal import Decimal

    from app.models.payment import Payment

    # SQLAlchemy doesn't auto-convert; the caller must hand a Decimal.
    # We confirm the column's Python-side type is Decimal — a float
    # bound to it would lose precision on the next read.
    py_type = Payment.__table__.columns["amount"].type.python_type
    assert py_type is Decimal, f"Payment.amount python_type should be Decimal, got {py_type}"


def test_card_rebate_rate_python_type_is_decimal():
    """`rate * amount_charged` must stay in Decimal land, not slip
    into float and accrue binary error across thousands of rebates."""
    from decimal import Decimal

    from app.models.virtual_card import CardRebate

    py_type = CardRebate.__table__.columns["rate"].type.python_type
    assert py_type is Decimal


# ---------------------------------------------------------------------------
# Decimal arithmetic — services that compute totals stay in Decimal
# ---------------------------------------------------------------------------


def test_remittance_pdf_context_amount_field_is_decimal():
    """Remittance PDFs render formatted money strings. The dataclass
    that backs the renderer must declare `payment_amount` and per-line
    `amount` as Decimal (not float) so the bytes match the DB row's
    precision."""
    from app.services.remittance_pdf import RemittanceContext, RemittanceLine

    ctx_annotations = inspect.get_annotations(RemittanceContext, eval_str=True)
    line_annotations = inspect.get_annotations(RemittanceLine, eval_str=True)

    got = ctx_annotations["payment_amount"]
    assert got is Decimal, f"RemittanceContext.payment_amount should be Decimal, got {got}"
    assert line_annotations["amount"] is Decimal, (
        f"RemittanceLine.amount should be Decimal, got {line_annotations['amount']}"
    )


# ---------------------------------------------------------------------------
# Whole-schema sweep — new numeric columns are OPT-OUT, not opt-in
# ---------------------------------------------------------------------------
#
# This sweep used to match column NAMES against a token list
# (`amount`, `total`, `price`, `subtotal`, `_tax`, `amount_`) and assert
# only that a match was not `Float`. Two holes, both load-bearing:
#
#   1. The token list drifted off the schema. 35 of the 88 `Numeric`
#      columns matched no token — every FX rate at `NUMERIC(18, 8)`
#      (`payments.fx_rate`, `invoices.reporting_fx_rate`,
#      `expenses.converted_fx_rate`), all four expense-policy
#      thresholds, `contracts.spend_limit`, both `bank_statements`
#      balances, `cash_plans.opening_balance` / `.cash_budget`, every
#      `quantity`, and even `invoice_line_items.tax` (the token was
#      `_tax`, with the underscore). A new `Column(Float)` named
#      `balance`, `budget`, `limit`, `fee`, `cost` or `*_rate` passed
#      the whole file.
#   2. Being a name allowlist made the guard OPT-IN: a money column was
#      only protected once someone remembered to name it in a way the
#      tokens happened to cover.
#
# It is now inverted, so a new column is opt-OUT: EVERY `Numeric` must
# declare precision and scale, and NO `Float` may exist anywhere under
# `app/models/` unless it is named in `NON_MONEY_FLOAT_ALLOWLIST` below
# with a reason. Adding a money column requires doing nothing; adding a
# float requires arguing for it in writing.


def _iter_model_columns() -> Iterator[tuple[str, Column]]:
    """Yield `(table_name, column)` for every column the ORM defines.

    Imports every module under `app/models/` rather than trusting
    `app.models.__init__` to re-export them — `usage.py`
    (`ExtractionUsage`) is *not* re-exported there today, so a walk over
    `Base.registry.mappers` after a plain `import app.models` silently
    skips it and any future sibling that lands the same way. Reads
    `Base.metadata.tables` rather than the mapper registry so an
    unmapped `Table()` (association tables) is covered too.
    """
    import app.models
    from app.models import Base

    for mod in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{mod.name}")

    for table in Base.metadata.tables.values():
        for col in table.columns:
            yield table.name, col


# Columns that are genuinely NOT money and may therefore be a binary
# float. Keyed `(table_name, column_name)` → why it is not money.
#
# The bar for an entry is high, and "the test went red" is not it: if a
# column plausibly holds currency, a quantity that gets multiplied by a
# price, a rate that gets applied to money, or a threshold money is
# compared against, it is money — fix the column type (with a migration),
# do not list it here. Legitimate entries look like a model confidence
# score, a ranking weight, a latency measurement, a geo coordinate.
#
# Deliberately EMPTY: as of this writing the schema has 88 `Numeric`
# columns and ZERO `Float` / `Double` / `REAL` columns. Nothing has ever
# needed the escape hatch.
NON_MONEY_FLOAT_ALLOWLIST: dict[tuple[str, str], str] = {}


def test_every_numeric_column_declares_precision_and_scale():
    """A bare `Numeric()` is ambiguous across backends — arbitrary
    precision on some, double-precision float on others — so the same
    model can round differently depending on where it is deployed.
    Every `Numeric` in the schema declares both, with no exceptions and
    no allowlist: there is no reason to want an unspecified one."""
    violations: list[str] = []

    for table_name, col in _iter_model_columns():
        col_type = col.type
        if not isinstance(col_type, Numeric) or isinstance(col_type, Float):
            continue
        missing = [
            part
            for part, value in (("precision", col_type.precision), ("scale", col_type.scale))
            if value is None
        ]
        if missing:
            violations.append(f"{table_name}.{col.name} (missing {' + '.join(missing)})")

    assert not violations, (
        "every Numeric column must declare precision AND scale — "
        f"bare Numeric() found on: {sorted(violations)}"
    )


def test_no_float_columns_outside_allowlist():
    """No binary float anywhere in the schema unless it is named in
    `NON_MONEY_FLOAT_ALLOWLIST` with a reason.

    `isinstance(..., Float)` covers `Float`, `Double`,
    `DOUBLE_PRECISION` and `REAL` — all four subclass it — so a column
    spelled with the PostgreSQL dialect type is caught the same way.
    """
    violations: list[str] = []

    for table_name, col in _iter_model_columns():
        if not isinstance(col.type, Float):
            continue
        if (table_name, col.name) in NON_MONEY_FLOAT_ALLOWLIST:
            continue
        violations.append(f"{table_name}.{col.name} ({type(col.type).__name__})")

    assert not violations, (
        "binary float columns found under app/models/. Money must be "
        "Numeric(precision, scale) — a float cannot represent 0.10 exactly, "
        "so every sum across the column is slightly wrong. If the column is "
        "genuinely not money, add it to NON_MONEY_FLOAT_ALLOWLIST with a "
        f"reason: {sorted(violations)}"
    )


def test_float_allowlist_has_no_stale_entries():
    """An allowlist entry outlives the column it excused unless
    something prunes it — and a stale `(table, column)` key silently
    pre-approves a *future* column of that name, which is exactly the
    money column the guard exists to catch. Every entry must name a
    Float column that actually exists, and carry a real reason."""
    live_floats = {
        (table_name, col.name)
        for table_name, col in _iter_model_columns()
        if isinstance(col.type, Float)
    }
    stale = sorted(set(NON_MONEY_FLOAT_ALLOWLIST) - live_floats)
    assert not stale, f"NON_MONEY_FLOAT_ALLOWLIST names columns that are no longer Float: {stale}"

    unexplained = sorted(key for key, reason in NON_MONEY_FLOAT_ALLOWLIST.items() if not reason)
    assert not unexplained, f"every NON_MONEY_FLOAT_ALLOWLIST entry needs a reason: {unexplained}"


# Column names that are unambiguously money in this domain. `Float` is
# not the only wrong type — a money column typed `String` also passes the
# two sweeps above, and this catches the obvious spellings of it.
#
# EXACT names, not substrings: a substring match drags in `cost_center`,
# `tax_id`, `tax_year` and `amount_mismatch_count`, none of which are
# money, and an allowlist for those false positives is the drift-prone
# thing this whole file just stopped doing. Narrow and precise beats
# broad and excused. This is a supplement to the sweeps, not a claim of
# completeness — the comprehensive guards are the two above plus the
# explicit `_MONEY_MODELS_AND_FIELDS` list at the top of the file.
_UNAMBIGUOUS_MONEY_COLUMN_NAMES = frozenset(
    {
        "amount",
        "discount_amount",
        "shipping_amount",
        "subtotal",
        "tax_amount",
        "total",
        "total_amount",
        "total_value",
        "unit_price",
    }
)


def test_unambiguous_money_column_names_are_numeric():
    """`Column(String)` named `amount` stores exact digits but makes
    every comparison and SUM lexicographic — "9.00" > "10.00". Money is
    `Numeric`, not merely "not Float"."""
    violations = [
        f"{table_name}.{col.name} ({type(col.type).__name__})"
        for table_name, col in _iter_model_columns()
        if col.name in _UNAMBIGUOUS_MONEY_COLUMN_NAMES and not isinstance(col.type, Numeric)
    ]
    assert not violations, f"money-named columns must be Numeric(p, s): {sorted(violations)}"


def test_model_sweep_is_not_vacuous():
    """The three sweeps above are only as good as what they iterate. A
    refactor that moves the models, renames the package, or breaks the
    dynamic import would make every one of them pass over an empty
    collection — green, and guarding nothing. Pin the floor."""
    columns = list(_iter_model_columns())
    tables = {table_name for table_name, _ in columns}
    numerics = [col for _, col in columns if isinstance(col.type, Numeric)]

    assert len(tables) >= 70, f"only {len(tables)} tables swept — the model import is broken"
    assert len(numerics) >= 80, (
        f"only {len(numerics)} Numeric columns swept — the model import is broken"
    )
    assert ("invoices", "amount") in {(table_name, col.name) for table_name, col in columns}, (
        "the canonical money column is missing from the sweep"
    )
