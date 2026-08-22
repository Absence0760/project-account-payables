"""Virtual-card response schemas keep money exact (Decimal, never float).

The DB columns are Numeric(15, 2) and the service layer is Decimal throughout,
but the response schemas used to retype money to `float` at the API boundary —
collapsing precision and violating the "money is exact" invariant. They now use
the shared MoneyAmount annotation: Decimal in Python, JSON *number* on the wire
(so the SPA contract is unchanged — distinct from the public /v1 string money).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.virtual_card import (
    CardDashboardResponse,
    CardResponse,
    RebateListResponse,
    RebateResponse,
    RebateStatusBreakdown,
)

# Every money field on each card response schema.
_MONEY_FIELDS = {
    CardResponse: ["amount_limit", "amount_charged"],
    CardDashboardResponse: [
        "active_cards_value",
        "spend_this_month",
        "rebates_this_month",
        "rebates_ytd",
        "projected_annual_rebates",
    ],
    RebateResponse: ["amount", "rate"],
    RebateListResponse: ["total"],
    RebateStatusBreakdown: ["pending_total", "confirmed_total", "paid_out_total"],
}


@pytest.mark.parametrize("model,fields", [(m, f) for m, f in _MONEY_FIELDS.items()])
def test_money_fields_are_not_typed_float(model, fields):
    # A bare `float` annotation is the regression we're guarding against.
    for field in fields:
        ann = model.model_fields[field].annotation
        assert ann is not float, f"{model.__name__}.{field} must not be a bare float"


def test_card_response_keeps_decimal_in_python():
    resp = CardResponse(
        id="1",
        invoice_id="2",
        vendor_id=None,
        card_provider="lithic",
        last_four="4242",
        amount_limit=Decimal("1234.56"),
        amount_charged=Decimal("1234.56"),
        currency="USD",
        status="active",
        expires_at=None,
        sent_at=None,
        charged_at=None,
        merchant_name=None,
        decline_reason=None,
        created_at="",
    )
    # In-Python value stays Decimal (exact) …
    assert isinstance(resp.amount_limit, Decimal)
    assert resp.amount_limit == Decimal("1234.56")
    # … and serialises to a JSON *number* (SPA contract), not a string.
    dumped = resp.model_dump(mode="json")
    assert isinstance(dumped["amount_limit"], (int, float))
    assert dumped["amount_limit"] == 1234.56


def test_rebate_rate_stays_exact_decimal():
    r = RebateResponse(
        id="1",
        virtual_card_id="2",
        amount=Decimal("15.00"),
        rate=Decimal("0.0125"),
        status="pending",
        period="2026-06",
        created_at="",
    )
    assert isinstance(r.rate, Decimal)
    assert r.rate == Decimal("0.0125")
    assert r.model_dump(mode="json")["rate"] == 0.0125


def test_dashboard_projection_default_is_decimal_zero():
    zero_breakdown = RebateStatusBreakdown(
        pending_total=Decimal("0"), confirmed_total=Decimal("0"), paid_out_total=Decimal("0")
    )
    d = CardDashboardResponse(
        active_cards=0,
        active_cards_value=Decimal("0"),
        spend_this_month=Decimal("0"),
        rebates_this_month=Decimal("0"),
        rebates_ytd=Decimal("0"),
        rebates_this_month_by_status=zero_breakdown,
        rebates_ytd_by_status=zero_breakdown,
    )
    assert isinstance(d.projected_annual_rebates, Decimal)
    assert d.model_dump(mode="json")["projected_annual_rebates"] == 0
