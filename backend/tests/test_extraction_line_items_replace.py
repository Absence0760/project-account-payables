"""Extraction REPLACES an invoice's line items — it never appends to them.

`POST /api/invoices/{id}/extract` is re-runnable on any `new` or `failed`
invoice, and `PUT /api/invoices/{id}/line-items` stays open until approval. So a
clerk can hand-key lines onto a manually-created (or extraction-disabled)
invoice and then hit "Extract" — the modal offers the button for exactly that
state. The insert loop in `services/extraction.run_extraction` used to `db.add`
the extracted lines with no delete, leaving BOTH sets on the row.

That is not a cosmetic duplication. Nothing recomputes the header `amount` from
the lines (deliberately — see `docs/line-total-reconciliation.md`), so the
doubled sum stops reconciling, `refresh_warnings` raises a `line_total_mismatch`
exception, and that type is in `PAYMENT_BLOCKING_EXCEPTION_TYPES`: the invoice
silently cannot enter a payment run. Any ERP push carries the duplicated lines
too.

The replace is guarded on a NON-EMPTY extraction: a run that found no lines is
not evidence there are none, and wiping a human's work on that basis would just
trade one data bug for another.

Runs against the opt-in `realdb` fixture (needs Postgres + MinIO).
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.models.invoice import Invoice, InvoiceLineItem

_HAND_KEYED = [
    {
        "description": "Hand-keyed A",
        "quantity": "1",
        "unit_price": "750.00",
        "total": "750.00",
    },
    {
        "description": "Hand-keyed B",
        "quantity": "1",
        "unit_price": "750.00",
        "total": "750.00",
    },
]

_MOCK_CONFIG = {"extraction": {"program_type": "byok", "provider": "mock"}}


async def _seed_invoice_with_hand_keyed_lines(realdb, *, number: str) -> str:
    """Manual invoice at `new`, with a file attached and two hand-keyed lines."""
    async with realdb.client(key="a", role="admin") as c:
        created = await c.post(
            "/api/invoices",
            json={
                "invoice_number": number,
                "vendor": "Line Replace Vendor",
                "amount": "1500.00",
                "currency": "USD",
            },
        )
        assert created.status_code == 201, created.text
        inv_id = created.json()["id"]

        attached = await c.post(
            f"/api/invoices/{inv_id}/file",
            files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4 fixture"), "application/pdf")},
        )
        assert attached.status_code == 201, attached.text

        saved = await c.put(f"/api/invoices/{inv_id}/line-items", json=_HAND_KEYED)
        assert saved.status_code == 200, saved.text
    return inv_id


async def _line_items(mk, inv_id) -> list[InvoiceLineItem]:
    async with mk() as s:
        return list(
            (await s.execute(select(InvoiceLineItem).where(InvoiceLineItem.invoice_id == inv_id)))
            .scalars()
            .all()
        )


async def _run_extraction(mk, inv_id) -> None:
    from app.services.extraction import run_extraction

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await run_extraction(s, inv, org_settings=_MOCK_CONFIG)


@pytest.mark.asyncio
async def test_extraction_replaces_hand_keyed_line_items(realdb):
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice_with_hand_keyed_lines(realdb, number="LIREPL-1")

    await _run_extraction(mk, inv_id)

    rows = await _line_items(mk, inv_id)
    assert len(rows) == 1, "extraction must replace the invoice's line items, not append"
    assert rows[0].description == "Professional services"

    # And the surviving set reconciles with the header the payment run pays.
    async with mk() as s:
        summed = (
            await s.execute(
                select(func.sum(InvoiceLineItem.total)).where(InvoiceLineItem.invoice_id == inv_id)
            )
        ).scalar_one()
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
    assert summed == inv.amount


@pytest.mark.asyncio
async def test_extraction_with_no_line_items_keeps_the_hand_keyed_ones(realdb):
    """The guard: a run that extracted nothing must not delete a human's work."""
    from app.services.extraction_adapters.base import ExtractedField, ExtractionResult

    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice_with_hand_keyed_lines(realdb, number="LIREPL-2")

    class _NoLinesAdapter:
        provider_name = "mock"

        async def extract(self, **_kwargs):
            return ExtractionResult(
                success=True,
                overall_confidence=0.9,
                vendor_name=ExtractedField("Line Replace Vendor", 0.9),
                invoice_number=ExtractedField("LIREPL-2", 0.9),
                amount=ExtractedField("1500.00", 0.9),
                line_items=[],
                provider="mock",
            )

    with patch(
        "app.services.extraction_adapters.get_extraction_adapter",
        return_value=_NoLinesAdapter(),
    ):
        await _run_extraction(mk, inv_id)

    rows = await _line_items(mk, inv_id)
    assert len(rows) == 2, "an empty extraction must not wipe hand-keyed line items"
    assert {r.description for r in rows} == {"Hand-keyed A", "Hand-keyed B"}
