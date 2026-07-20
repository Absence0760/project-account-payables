"""Vendor matching service — fuzzy match vendor names from invoices against existing vendors."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.vendor import Vendor


def _normalize(name: str) -> str:
    """Normalize a vendor name for comparison."""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in (
        " inc",
        " inc.",
        " llc",
        " ltd",
        " ltd.",
        " corp",
        " corp.",
        " co",
        " co.",
        " company",
        " group",
        " plc",
        " gmbh",
        " pty",
        " pty.",
        " limited",
        " incorporated",
    ):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
    # Remove punctuation
    name = "".join(c for c in name if c.isalnum() or c == " ")
    # Collapse whitespace
    return " ".join(name.split())


def _similarity(a: str, b: str) -> float:
    """Simple similarity score between two normalized strings (0-1).

    Uses token overlap (Jaccard similarity on words).
    """
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


async def match_vendor(
    db: AsyncSession,
    vendor_name: str,
    vendor_tax_id: str | None = None,
    vendor_address: str | None = None,
    organization_id: uuid.UUID | None = None,
) -> tuple[Vendor | None, float]:
    """Find the best matching vendor for a given name.

    Returns (vendor, confidence) where confidence is 0-1.
    Returns (None, 0) if no reasonable match found.
    """
    if not vendor_name or not vendor_name.strip():
        return None, 0.0

    # First: try exact tax_id match (highest confidence)
    if vendor_tax_id:
        result = await db.execute(
            select(Vendor).where(
                Vendor.tax_id == vendor_tax_id,
                Vendor.status.in_(["active", "unverified"]),
            )
        )
        tax_match = result.scalar_one_or_none()
        if tax_match:
            return tax_match, 1.0

    # Second: try exact name match (case-insensitive)
    result = await db.execute(
        select(Vendor).where(
            func.lower(Vendor.name) == vendor_name.lower().strip(),
            Vendor.status.in_(["active", "unverified"]),
        )
    )
    exact_match = result.scalar_one_or_none()
    if exact_match:
        return exact_match, 0.98

    # Third: fuzzy match against all active vendors
    result = await db.execute(select(Vendor).where(Vendor.status.in_(["active", "unverified"])))
    vendors = result.scalars().all()

    normalized_input = _normalize(vendor_name)
    best_vendor: Vendor | None = None
    best_score = 0.0

    for v in vendors:
        score = _similarity(normalized_input, _normalize(v.name))

        # Address can only ever *boost* confidence — never drag a strong name
        # match down. A non-matching listed address must not penalize a perfect
        # name match (which the old `name*0.8 + addr*0.2` blend did, turning a
        # 1.0 into 0.8). Take the better of the name score and the blend.
        if vendor_address and v.address:
            addr_score = _similarity(
                vendor_address.lower(),
                v.address.lower(),
            )
            score = max(score, score * 0.8 + addr_score * 0.2)

        if score > best_score:
            best_score = score
            best_vendor = v

    # Only return if score is above threshold
    if best_score >= 0.6:
        return best_vendor, best_score

    return None, 0.0


async def match_and_link_vendor(
    db: AsyncSession,
    invoice: Invoice,
    organization_id: uuid.UUID,
    source: str = "ai_extracted",
) -> tuple[Vendor | None, str]:
    """Match an invoice's vendor_name to an existing vendor and link them.

    ``source`` stamps the provenance of a vendor this call has to create
    (``Vendor.source``): ``ai_extracted`` for the extraction pipeline,
    ``manual`` when a human keyed the invoice in by hand. It does not affect
    matching — only the row written when nothing matches.

    Returns (vendor, action) where action is:
    - "linked" — matched to existing vendor
    - "created" — new unverified vendor created
    - "none" — no vendor name on invoice
    """
    if not invoice.vendor_name:
        return None, "none"

    vendor, confidence = await match_vendor(
        db,
        vendor_name=invoice.vendor_name,
        vendor_tax_id=invoice.vendor_tax_id,
        vendor_address=invoice.vendor_address,
    )

    if vendor and confidence >= 0.8:
        # High confidence — auto-link
        invoice.vendor_id = vendor.id
        return vendor, "linked"

    if vendor and confidence >= 0.6:
        # Medium confidence — link but flag for review
        invoice.vendor_id = vendor.id
        return vendor, "linked"

    # No match — create unverified vendor from invoice data. It inherits the
    # invoice's entity so the auto-created vendor lands in the same subsidiary
    # as the invoice it came from (multi-entity Phase 2).
    new_vendor = Vendor(
        name=invoice.vendor_name,
        address=invoice.vendor_address,
        tax_id=invoice.vendor_tax_id,
        status="unverified",
        source=source,
        organization_id=organization_id,
        entity_id=invoice.entity_id,
    )
    db.add(new_vendor)
    await db.flush()

    invoice.vendor_id = new_vendor.id
    return new_vendor, "created"
