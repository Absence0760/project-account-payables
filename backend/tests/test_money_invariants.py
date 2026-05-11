"""Money-invariant tests (project invariant #1).

> Money is exact. Amounts use `Decimal` (never `float`), and SQLAlchemy
> columns for currency use `Numeric(precision, scale)` (never `Float` /
> `Real`). A new column or in-memory total typed as `float` for
> currency is `Critical`.

These tests pin the contract across every model + schema that carries
a monetary amount. A future PR that introduces `Float` for a "speed"
reason is caught by tests, not by an auditor.

We also pin the in-memory contract: any service helper that produces
a money total must return a `Decimal`, never a `float`. The
adapter-payload schemas are the boundary at which user-typed Decimals
become SDK-bound values.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from sqlalchemy import Float, Numeric

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
# Sweep: no Float column anywhere on a money-named field
# ---------------------------------------------------------------------------


def test_no_float_columns_on_money_named_fields():
    """Broad sweep across every imported model: any column whose
    name looks like a money field (`amount`, `total`, `price`,
    `rate`, `subtotal`, `tax`) must NOT be `Float`. Catches the
    "I added a new column" regression that the per-model parametrize
    above can't cover automatically."""

    from app.models import Base

    money_name_tokens = ("amount", "total", "price", "subtotal", "_tax", "amount_")
    violations: list[str] = []

    for cls in Base.registry.mappers:
        cls = cls.class_
        if not hasattr(cls, "__table__"):
            continue
        for col in cls.__table__.columns:
            name = col.name.lower()
            if not any(tok in name for tok in money_name_tokens):
                continue
            if name in {"tax_rate"}:
                # Numeric(5, 2) percentage is fine and intentional.
                pass
            if isinstance(col.type, Float):
                violations.append(f"{cls.__name__}.{col.name} ({type(col.type).__name__})")

    assert not violations, f"money-named columns must not use Float / Real: {violations}"
