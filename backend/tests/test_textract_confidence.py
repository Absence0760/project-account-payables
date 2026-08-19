"""Textract reports two confidences; the adapter was reading the wrong one.

``Type.Confidence`` answers "is this field the TOTAL?".
``ValueDetection.Confidence`` answers "does it really say 1500.00?".

Only the second is confidence in the extracted VALUE, and only the first was
read — so a crisply-classified but barely-legible figure (type 99.5, value 41.0)
arrived as ``0.995``. That is the number ``decide_auto_approve`` gates on and
the number the per-field review bands use, so an amount the OCR was 41% sure of
was presented as a 99% read and cleared the 0.95 touchless threshold.

Both must hold for the mapped field to be worth trusting, so the adapter now
takes the lower of the two.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest


def _install_fake_boto3(response: dict, monkeypatch):
    class _Client:
        def analyze_expense(self, Document):  # noqa: N803 — boto3's kwarg name
            return response

    module = types.ModuleType("boto3")
    module.client = lambda *a, **kw: _Client()
    monkeypatch.setitem(sys.modules, "boto3", module)


def _summary_field(field_type, text, type_conf, value_conf):
    return {
        "Type": {"Text": field_type, "Confidence": type_conf},
        "ValueDetection": {"Text": text, "Confidence": value_conf},
    }


def _extract(response, monkeypatch):
    _install_fake_boto3(response, monkeypatch)
    from app.services.extraction_adapters.aws_textract import AWSTextractAdapter

    return asyncio.run(AWSTextractAdapter({}).extract(file_bytes=b"%PDF-1.4", file_key="x.pdf"))


def test_a_confidently_typed_but_poorly_read_value_is_not_high_confidence(monkeypatch):
    result = _extract(
        {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        _summary_field("TOTAL", "1500.00", 99.5, 41.0),
                        _summary_field("INVOICE_RECEIPT_ID", "INV-1", 99.0, 38.0),
                        _summary_field("VENDOR_NAME", "Acme", 99.0, 40.0),
                    ],
                    "LineItemGroups": [],
                }
            ]
        },
        monkeypatch,
    )
    assert result.amount.value == "1500.00"
    assert result.amount.confidence == pytest.approx(0.41)
    # Nowhere near the 0.95 touchless threshold it used to clear at 0.99.
    assert result.overall_confidence < 0.5


def test_a_genuinely_clean_read_stays_high_confidence(monkeypatch):
    result = _extract(
        {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        _summary_field("TOTAL", "1500.00", 99.5, 99.1),
                        _summary_field("INVOICE_RECEIPT_ID", "INV-1", 99.0, 98.7),
                        _summary_field("VENDOR_NAME", "Acme", 99.0, 99.0),
                    ],
                    "LineItemGroups": [],
                }
            ]
        },
        monkeypatch,
    )
    assert result.amount.confidence == pytest.approx(0.991)
    assert result.overall_confidence > 0.98


def test_line_item_fields_take_the_same_lower_bound(monkeypatch):
    result = _extract(
        {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [],
                    "LineItemGroups": [
                        {
                            "LineItems": [
                                {
                                    "LineItemExpenseFields": [
                                        _summary_field("ITEM", "Widget", 99.0, 55.0),
                                        _summary_field("PRICE", "10.00", 98.0, 60.0),
                                    ]
                                }
                            ]
                        }
                    ],
                }
            ]
        },
        monkeypatch,
    )
    li = result.line_items[0]
    assert li.description.confidence == pytest.approx(0.55)
    assert li.total.confidence == pytest.approx(0.60)


def test_a_missing_or_junk_confidence_is_conservative(monkeypatch):
    result = _extract(
        {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        # No ValueDetection.Confidence at all — we cannot tell
                        # how good the read was, so it is not a good read.
                        {
                            "Type": {"Text": "TOTAL", "Confidence": 99.5},
                            "ValueDetection": {"Text": "1500.00"},
                        },
                        {
                            "Type": {"Text": "VENDOR_NAME", "Confidence": "junk"},
                            "ValueDetection": {"Text": "Acme", "Confidence": 99.0},
                        },
                    ],
                    "LineItemGroups": [],
                }
            ]
        },
        monkeypatch,
    )
    assert result.amount.confidence == 0.0
    assert result.vendor_name.confidence == 0.0
