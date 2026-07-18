"""Regression tests for issue #133 — refresh_warnings clobbered upstream
extraction warnings.

`run_extraction` appends `extraction_self_correction` / `gl_account_invalid` /
`duplicate_similar` entries directly onto `invoice.warnings`, then calls
`refresh_warnings` as its LAST step. `refresh_warnings` used to build a brand
new list from `[]` and do `invoice.warnings = warnings or None` — an
unconditional overwrite that silently erased whatever extraction had just
appended, so a self-correction / hallucinated-GL / semantic-duplicate warning
never reached the reviewer or any exception row.

The existing `test_extraction_gl_validation.py` suite mocks `refresh_warnings`
to a no-op, which is exactly why this real clobbering path was never caught —
these tests call the real function.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _invoice(**overrides):
    base = dict(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        vendor_name="Acme Corp",
        invoice_number="INV-100",
        amount=Decimal("500.00"),
        currency="USD",
        invoice_date=date(2026, 5, 1),
        due_date=date(2026, 5, 15),
        status="new",
        po_number=None,
        po_match=None,
        contract_id=None,
        warnings=None,
        vendor_id=None,  # keep vendor-scoped rules out of scope for these tests
        remit_to_address=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_db(*, dup_count=0):
    def _result(scalar=None, scalar_one=None, scalars_all=None):
        r = MagicMock()
        r.scalar = MagicMock(return_value=scalar)
        r.scalar_one_or_none = MagicMock(return_value=scalar_one)
        r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=scalars_all or [])))
        return r

    async def _execute(*args, **_kw):
        q = str(args[0]).lower() if args else ""
        if "from invoices" in q and "count(" in q:
            return _result(scalar=dup_count)
        if "count(" in q:
            return _result(scalar=0)  # no prior exception
        return _result()

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _types(warnings):
    return {w["type"] for w in (warnings or [])}


@pytest.mark.asyncio
async def test_extraction_self_correction_survives_refresh_warnings():
    """A self-correction warning appended by extraction BEFORE refresh_warnings
    runs must still be present in the persisted list afterward."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(
        warnings=[
            {
                "type": "extraction_self_correction",
                "severity": "warning",
                "message": "Line items don't sum to total",
                "check": "line_item_sum",
            }
        ]
    )
    db = _make_db()

    result = await refresh_warnings(db, inv)

    assert "extraction_self_correction" in _types(result)
    assert "extraction_self_correction" in _types(inv.warnings)


@pytest.mark.asyncio
async def test_gl_account_invalid_survives_refresh_warnings():
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(
        warnings=[
            {
                "type": "gl_account_invalid",
                "severity": "warning",
                "message": "AI suggested GL code(s) not in active chart: 9999",
                "codes": ["9999"],
            }
        ]
    )
    db = _make_db()

    result = await refresh_warnings(db, inv)

    assert "gl_account_invalid" in _types(result)


@pytest.mark.asyncio
async def test_duplicate_similar_survives_refresh_warnings():
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(
        warnings=[
            {
                "type": "duplicate_similar",
                "severity": "warning",
                "message": "Potential duplicate: 92% match to INV-099",
                "related_invoices": [],
            }
        ]
    )
    db = _make_db()

    result = await refresh_warnings(db, inv)

    assert "duplicate_similar" in _types(result)


@pytest.mark.asyncio
async def test_all_three_upstream_categories_survive_together():
    """The realistic extraction shape: all three upstream warnings landed on
    the same invoice before refresh_warnings runs once at the end."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(
        warnings=[
            {"type": "extraction_self_correction", "severity": "warning", "message": "m1"},
            {"type": "gl_account_invalid", "severity": "warning", "message": "m2", "codes": []},
            {"type": "duplicate_similar", "severity": "warning", "message": "m3"},
        ]
    )
    db = _make_db()

    result = await refresh_warnings(db, inv)

    assert {"extraction_self_correction", "gl_account_invalid", "duplicate_similar"} <= _types(
        result
    )


@pytest.mark.asyncio
async def test_refresh_warnings_still_fully_recomputes_its_own_categories():
    """Preserving upstream categories must not turn into preserving
    EVERYTHING — a stale owned-category warning that no longer applies (here:
    a missing-vendor-name flag on an invoice that now has one) must NOT
    survive; refresh_warnings still rebuilds its own categories from
    scratch."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(
        vendor_name="Acme Corp",  # now valid — the stale warning no longer applies
        warnings=[
            {"type": "missing_field", "severity": "error", "message": "Missing vendor name"}
        ],
    )
    db = _make_db()

    result = await refresh_warnings(db, inv)

    assert "missing_field" not in _types(result)


@pytest.mark.asyncio
async def test_no_prior_warnings_still_works():
    """Baseline: an invoice with warnings=None still gets a normal fresh
    warnings list (the seed-from-upstream change must not require a prior
    list to exist)."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(warnings=None, vendor_name="")  # trip a real owned warning
    db = _make_db()

    result = await refresh_warnings(db, inv)

    assert "missing_field" in _types(result)
