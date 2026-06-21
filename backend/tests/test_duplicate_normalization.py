"""Rule-based duplicate detection normalizes case + whitespace.

The always-on first-gate duplicate check (services/invoice_warnings) used
strict byte-equality on vendor_name + invoice_number, so a re-submission that
differed only in casing or a trailing space sailed through as unique. These
realdb tests prove the normalized (lower + trim) match catches it, and that a
genuinely different invoice number is NOT a false positive.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.services.invoice_warnings import refresh_warnings

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _add_invoice(mk, org_id, *, vendor, number):
    async with mk() as s:
        ent = await _default_entity_id(s)
        inv = Invoice(
            organization_id=org_id,
            entity_id=ent,
            vendor_name=vendor,
            invoice_number=number,
            amount=Decimal("123.45"),
            currency="USD",
            status=InvoiceStatus.new,
        )
        s.add(inv)
        await s.commit()


@pytest.mark.asyncio
async def test_duplicate_detection_normalizes_case_and_whitespace(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    # First invoice, canonical casing/spacing.
    await _add_invoice(mk, org_id, vendor="ZZ Normalize Co", number="ZZ-DUP-1")

    # Second invoice differs ONLY by case + a trailing space — a duplicate that
    # strict equality missed.
    async with mk() as s:
        ent = await _default_entity_id(s)
        dup = Invoice(
            organization_id=org_id,
            entity_id=ent,
            vendor_name="zz normalize co",
            invoice_number="ZZ-DUP-1 ",
            amount=Decimal("123.45"),
            currency="USD",
            status=InvoiceStatus.new,
        )
        s.add(dup)
        await s.flush()
        warnings = await refresh_warnings(s, dup)
        await s.commit()

    assert any(w["type"] == "duplicate" for w in warnings), warnings


@pytest.mark.asyncio
async def test_distinct_invoice_number_is_not_flagged_duplicate(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _add_invoice(mk, org_id, vendor="ZZ Distinct Co", number="ZZ-UNIQUE-A")

    async with mk() as s:
        ent = await _default_entity_id(s)
        other = Invoice(
            organization_id=org_id,
            entity_id=ent,
            vendor_name="ZZ Distinct Co",
            invoice_number="ZZ-UNIQUE-B",  # genuinely different number
            amount=Decimal("123.45"),
            currency="USD",
            status=InvoiceStatus.new,
        )
        s.add(other)
        await s.flush()
        warnings = await refresh_warnings(s, other)
        await s.commit()

    assert not any(w["type"] == "duplicate" for w in warnings), warnings
