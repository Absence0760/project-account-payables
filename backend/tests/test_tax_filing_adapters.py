"""Tests for the 1099 e-filing adapters (mock + tax1099 skeleton)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.tax_filing_adapters import FilingFormPayload, get_tax_filing_adapter
from app.services.tax_filing_adapters.base import (
    BATCH_ACCEPTED,
    BATCH_PARTIAL,
    BATCH_REJECTED,
)


def _form(*, vendor_id="v1", tin="12-3456789", amount="1500.00", name="Acme LLC", year=2026):
    return FilingFormPayload(
        vendor_id=vendor_id,
        form_type="1099-NEC",
        recipient_name=name,
        recipient_tin=tin,
        box_amount=Decimal(amount),
        tax_year=year,
    )


@pytest.mark.asyncio
async def test_mock_accepts_well_formed_batch():
    adapter = get_tax_filing_adapter({"provider": "mock"})
    result = await adapter.submit_batch(
        tax_year=2026, forms=[_form(), _form(vendor_id="v2")], idempotency_key="k1"
    )
    assert result.status == BATCH_ACCEPTED
    assert result.accepted_count == 2
    assert result.rejected_count == 0
    assert result.confirmation_number is not None


@pytest.mark.asyncio
async def test_mock_is_idempotent_on_key():
    adapter = get_tax_filing_adapter(None)  # defaults to mock
    r1 = await adapter.submit_batch(tax_year=2026, forms=[_form()], idempotency_key="same-key")
    r2 = await adapter.submit_batch(tax_year=2026, forms=[_form()], idempotency_key="same-key")
    # Same key → same deterministic confirmation number (the adapter half of
    # the idempotency story; the API layer enforces the DB half).
    assert r1.confirmation_number == r2.confirmation_number


@pytest.mark.asyncio
async def test_mock_different_keys_differ():
    adapter = get_tax_filing_adapter({"provider": "mock"})
    r1 = await adapter.submit_batch(tax_year=2026, forms=[_form()], idempotency_key="a")
    r2 = await adapter.submit_batch(tax_year=2026, forms=[_form()], idempotency_key="b")
    assert r1.confirmation_number != r2.confirmation_number


@pytest.mark.asyncio
async def test_mock_rejects_bad_tin_form():
    adapter = get_tax_filing_adapter({"provider": "mock"})
    result = await adapter.submit_batch(
        tax_year=2026,
        forms=[_form(), _form(vendor_id="v2", tin="00-0000000")],
        idempotency_key="k",
    )
    assert result.status == BATCH_PARTIAL
    assert result.accepted_count == 1
    assert result.rejected_count == 1
    bad = next(f for f in result.forms if f.vendor_id == "v2")
    assert bad.accepted is False
    assert bad.reason_code == "tin_invalid"


@pytest.mark.asyncio
async def test_mock_rejects_non_positive_amount():
    adapter = get_tax_filing_adapter({"provider": "mock"})
    result = await adapter.submit_batch(
        tax_year=2026, forms=[_form(amount="0.00")], idempotency_key="k"
    )
    assert result.status == BATCH_REJECTED
    assert result.confirmation_number is None


@pytest.mark.asyncio
async def test_mock_empty_batch_is_rejected():
    adapter = get_tax_filing_adapter({"provider": "mock"})
    result = await adapter.submit_batch(tax_year=2026, forms=[], idempotency_key="k")
    assert result.status == BATCH_REJECTED
    assert result.submitted_count == 0


@pytest.mark.asyncio
async def test_mock_result_carries_no_tin():
    adapter = get_tax_filing_adapter({"provider": "mock"})
    result = await adapter.submit_batch(tax_year=2026, forms=[_form()], idempotency_key="k")
    assert "123456789" not in str(result.to_dict())


@pytest.mark.asyncio
async def test_tax1099_without_key_raises():
    adapter = get_tax_filing_adapter({"provider": "tax1099"})  # no api_key
    with pytest.raises(RuntimeError):
        await adapter.submit_batch(tax_year=2026, forms=[_form()], idempotency_key="k")
