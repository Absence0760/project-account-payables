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


@pytest.mark.asyncio
async def test_duplicate_matched_by_vendor_id_across_name_spellings(realdb):
    """A vendor with two name spellings (same STABLE vendor_id) resending the
    same invoice number must be flagged — the vendor_id leg catches what the
    free-text vendor_name match misses."""
    from app.models.vendor import Vendor

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    async with mk() as s:
        ent = await _default_entity_id(s)
        vendor = Vendor(
            organization_id=org_id,
            entity_id=ent,
            name="ZZ Acme Corp",
            status="active",
        )
        s.add(vendor)
        await s.flush()
        vendor_id = vendor.id
        first = Invoice(
            organization_id=org_id,
            entity_id=ent,
            vendor_id=vendor_id,
            vendor_name="ZZ Acme Corp",
            invoice_number="ZZ-VID-1",
            amount=Decimal("123.45"),
            currency="USD",
            status=InvoiceStatus.new,
        )
        s.add(first)
        await s.commit()

    async with mk() as s:
        ent = await _default_entity_id(s)
        # Same vendor_id, a DIFFERENT name spelling, same invoice number.
        dup = Invoice(
            organization_id=org_id,
            entity_id=ent,
            vendor_id=vendor_id,
            vendor_name="ZZ Acme Corporation",  # differs from "ZZ Acme Corp"
            invoice_number="ZZ-VID-1",
            amount=Decimal("123.45"),
            currency="USD",
            status=InvoiceStatus.new,
        )
        s.add(dup)
        await s.flush()
        warnings = await refresh_warnings(s, dup)
        await s.commit()

    assert any(w["type"] == "duplicate" for w in warnings), warnings


# ---------- Pure normalization rule (no DB) -------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("INV-001", "inv-1"),
        ("inv-1", "inv-1"),
        ("INV-001 ", "inv-1"),
        ("INV 001", "inv-1"),
        ("INV_001", "inv-1"),
        ("INV--001", "inv-1"),
        ("INV.001", "inv-1"),
        ("#001", "1"),
        ("001", "1"),
        ("000", "0"),  # a digit run never collapses to nothing
        ("INV-0", "inv-0"),
        ("INV-100", "inv-100"),  # only LEADING zeros go
        ("2026-0007", "2026-7"),
        ("INV-1-2", "inv-1-2"),  # separator runs collapse, they don't vanish
        ("INV-12", "inv-12"),
        (None, None),
        ("", None),
        ("   ", None),
        ("---", None),
    ],
)
def test_normalize_invoice_number(raw, expected):
    from app.services.invoice_warnings import normalize_invoice_number

    assert normalize_invoice_number(raw) == expected


def test_normalize_keeps_genuinely_different_numbers_apart():
    """The line between 'normalize' and 'guess'.

    Non-digit characters are never stripped, so a shared numeric tail is not
    enough to collide two numbers.
    """
    from app.services.invoice_warnings import normalize_invoice_number as n

    assert n("INV-1") != n("PO-1")
    assert n("INV-1") != n("INV-2")
    assert n("INV-1-2") != n("INV-12")
    assert n("INV-100") != n("INV-1")


def test_letter_skeleton_is_a_superset_invariant():
    """Anything that normalizes equal must share the SQL prefilter's skeleton,
    or the prefilter would drop real matches."""
    from app.services.invoice_warnings import (
        invoice_number_letter_skeleton,
        normalize_invoice_number,
    )

    pairs = [("INV-001", "inv-1"), ("2026-0007", "2026/7"), ("#001", "1")]
    for a, b in pairs:
        assert normalize_invoice_number(a) == normalize_invoice_number(b), (a, b)
        assert invoice_number_letter_skeleton(a) == invoice_number_letter_skeleton(b), (a, b)


# ---------- Leading-zero duplicates against a real tenant DB --------------


@pytest.mark.asyncio
async def test_leading_zero_variant_is_flagged_duplicate(realdb):
    """`INV-001` then `INV-1` from the same vendor is one payable, not two."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _add_invoice(mk, org_id, vendor="ZZ Leading Zero Co", number="ZZLZ-001")

    async with mk() as s:
        ent = await _default_entity_id(s)
        dup = Invoice(
            organization_id=org_id,
            entity_id=ent,
            vendor_name="ZZ Leading Zero Co",
            invoice_number="ZZLZ-1",
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
async def test_leading_zero_variant_raises_one_deduped_exception(realdb):
    """The widened match reuses `_ensure_exception`, so a recompute must not
    pile up a second `duplicate` row."""
    from app.models.exception import Exception as APException

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _add_invoice(mk, org_id, vendor="ZZ Dedupe Co", number="ZZDD-0042")

    async with mk() as s:
        ent = await _default_entity_id(s)
        dup = Invoice(
            organization_id=org_id,
            entity_id=ent,
            vendor_name="ZZ Dedupe Co",
            invoice_number="ZZDD-42",
            amount=Decimal("123.45"),
            currency="USD",
            status=InvoiceStatus.new,
        )
        s.add(dup)
        await s.flush()
        await refresh_warnings(s, dup)
        await refresh_warnings(s, dup)  # recompute — must not duplicate the row
        await s.commit()
        dup_id = dup.id

    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(APException).where(
                        APException.invoice_id == dup_id,
                        APException.exception_type == "duplicate",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, rows


@pytest.mark.asyncio
async def test_leading_zero_variant_from_another_vendor_is_not_flagged(realdb):
    """Normalization widens the match, so the vendor scope is what keeps it
    safe — a different supplier's `INV-1` is not our `INV-001`."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _add_invoice(mk, org_id, vendor="ZZ Vendor One", number="ZZXV-0007")

    async with mk() as s:
        ent = await _default_entity_id(s)
        other = Invoice(
            organization_id=org_id,
            entity_id=ent,
            vendor_name="ZZ Vendor Two",  # different supplier entirely
            invoice_number="ZZXV-7",
            amount=Decimal("123.45"),
            currency="USD",
            status=InvoiceStatus.new,
        )
        s.add(other)
        await s.flush()
        warnings = await refresh_warnings(s, other)
        await s.commit()

    assert not any(w["type"] == "duplicate" for w in warnings), warnings


@pytest.mark.asyncio
async def test_trailing_zero_number_is_not_flagged_duplicate(realdb):
    """`ZZTZ-100` must not collide with `ZZTZ-1` — only leading zeros go."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _add_invoice(mk, org_id, vendor="ZZ Trailing Co", number="ZZTZ-1")

    async with mk() as s:
        ent = await _default_entity_id(s)
        other = Invoice(
            organization_id=org_id,
            entity_id=ent,
            vendor_name="ZZ Trailing Co",
            invoice_number="ZZTZ-100",
            amount=Decimal("123.45"),
            currency="USD",
            status=InvoiceStatus.new,
        )
        s.add(other)
        await s.flush()
        warnings = await refresh_warnings(s, other)
        await s.commit()

    assert not any(w["type"] == "duplicate" for w in warnings), warnings
