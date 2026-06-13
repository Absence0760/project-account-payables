"""EInvoiceExtractionAdapter — maps a parsed document to ExtractionResult."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.services.extraction import _clean_date, _clean_decimal, _normalize_payment_method
from app.services.extraction_adapters.einvoice_adapter import EInvoiceExtractionAdapter

_FIX = Path(__file__).parent / "fixtures" / "e_invoice"
_UBL = (_FIX / "ubl_invoice.xml").read_bytes()
_MALFORMED = (_FIX / "malformed_ubl.xml").read_bytes()


@pytest.mark.asyncio
async def test_adapter_maps_ubl_to_result():
    adapter = EInvoiceExtractionAdapter({})
    result = await adapter.extract(
        file_bytes=_UBL, file_key="ubl_invoice.xml", mime_type="application/xml"
    )

    assert result.success is True
    assert result.provider == "einvoice"
    assert result.overall_confidence == 1.0

    # Every present field carries confidence 1.0.
    assert result.invoice_number.value == "INV-2024-0042"
    assert result.invoice_number.confidence == 1.0
    assert result.vendor_name.value == "Seller GmbH"
    assert result.vendor_name.confidence == 1.0
    assert result.vendor_tax_id.value == "DE123456789"
    assert result.currency.value == "EUR"
    assert result.po_number.value == "PO-7788"
    assert result.reference_number.value == "BUYER-REF-99"
    assert result.payment_terms.value == "Net 30 days"


@pytest.mark.asyncio
async def test_adapter_amounts_reparse_to_decimal():
    adapter = EInvoiceExtractionAdapter({})
    result = await adapter.extract(file_bytes=_UBL, file_key="ubl_invoice.xml")

    # The cleaners in extraction.py re-parse the string outputs to Decimal.
    assert _clean_decimal(result.amount.value) == Decimal("1190.00")
    assert _clean_decimal(result.subtotal.value) == Decimal("1000.00")
    assert _clean_decimal(result.tax_amount.value) == Decimal("190.00")
    assert _clean_decimal(result.tax_rate.value) == Decimal("19.00")  # single distinct rate
    assert _clean_date(result.invoice_date.value).isoformat() == "2024-03-15"
    assert _clean_date(result.due_date.value).isoformat() == "2024-04-14"


@pytest.mark.asyncio
async def test_adapter_maps_payment_means_code():
    adapter = EInvoiceExtractionAdapter({})
    result = await adapter.extract(file_bytes=_UBL, file_key="ubl_invoice.xml")
    # UNCL4461 code 30 (credit transfer) → ach; survives the downstream cleaner.
    assert result.payment_method.value == "ach"
    assert _normalize_payment_method(result.payment_method.value) == "ach"


@pytest.mark.asyncio
async def test_adapter_line_items():
    adapter = EInvoiceExtractionAdapter({})
    result = await adapter.extract(file_bytes=_UBL, file_key="ubl_invoice.xml")
    assert len(result.line_items) == 2
    li = result.line_items[0]
    assert li.line_number == 1
    assert li.description.value == "Widget A"
    assert li.item_code.value == "SKU-A"
    assert _clean_decimal(li.quantity.value) == Decimal("10")
    assert _clean_decimal(li.unit_price.value) == Decimal("60.00")
    assert _clean_decimal(li.total.value) == Decimal("600.00")
    assert _clean_decimal(li.tax.value) == Decimal("114.00")
    assert li.description.confidence == 1.0


@pytest.mark.asyncio
async def test_adapter_raw_response_has_no_pii():
    adapter = EInvoiceExtractionAdapter({})
    result = await adapter.extract(file_bytes=_UBL, file_key="ubl_invoice.xml")
    assert result.raw_response == {"e_invoice_format": "ubl", "root_tag": "Invoice"}
    # No tax id / address anywhere in the raw_response.
    blob = str(result.raw_response)
    assert "DE123456789" not in blob
    assert "Hauptstrasse" not in blob


@pytest.mark.asyncio
async def test_adapter_malformed_returns_failure_naming_fields_only():
    adapter = EInvoiceExtractionAdapter({})
    result = await adapter.extract(file_bytes=_MALFORMED, file_key="malformed_ubl.xml")
    assert result.success is False
    assert result.provider == "einvoice"
    # Missing invoice_number + seller.name (no PartyName) surface as field codes.
    assert "invoice_number: missing" in result.error
    assert "seller.name: missing" in result.error
    # No PII leaked.
    assert "Berlin" not in result.error


@pytest.mark.asyncio
async def test_adapter_non_structured_returns_failure():
    adapter = EInvoiceExtractionAdapter({})
    result = await adapter.extract(file_bytes=b"not an invoice", file_key="x.txt")
    assert result.success is False
    assert "structured" in result.error


@pytest.mark.asyncio
async def test_test_connection_true():
    adapter = EInvoiceExtractionAdapter({})
    assert await adapter.test_connection() is True
