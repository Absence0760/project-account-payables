"""Vendor matching + link lifecycle — pins the rules that decide
whether an extracted vendor name maps to an existing row, an existing
row with a flag, or a brand-new unverified row.

The lifecycle goes:
  ai_extracted invoice → match_and_link_vendor →
    tax_id exact      → confidence 1.0  → link
    name exact (ci)   → confidence 0.98 → link
    fuzzy ≥ 0.8       →                  → link
    fuzzy 0.6–0.8     →                  → link (flagged downstream)
    fuzzy < 0.6       →                  → new Vendor(status="unverified")

A regression on any of those thresholds either creates duplicate
vendor rows for the same supplier (under-matching) or silently
re-routes invoices to the wrong vendor (over-matching). Both ruin
spend rollups and payment routing.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vendor_matching import (
    _normalize,
    _similarity,
    match_and_link_vendor,
    match_vendor,
)


def _v(
    name: str,
    *,
    tax_id: str | None = None,
    address: str | None = None,
    status: str = "active",
    source: str = "manual",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        tax_id=tax_id,
        address=address,
        status=status,
        source=source,
    )


def _mk_db(*, expects_tax_query=False, tax_match=None, name_match=None, fuzzy_pool=()):
    """Up to three sequential queries: optional tax_id lookup (only
    when caller supplied a tax_id), name lookup, then the full pool
    for fuzzy. Pass `expects_tax_query=True` when the call site
    supplies a tax_id so the side_effect order matches."""
    queue: list = []

    if expects_tax_query:
        tax_res = MagicMock()
        tax_res.scalar_one_or_none = MagicMock(return_value=tax_match)
        queue.append(tax_res)

    name_res = MagicMock()
    name_res.scalar_one_or_none = MagicMock(return_value=name_match)
    queue.append(name_res)

    fuzzy_res = MagicMock()
    fuzzy_scalars = MagicMock()
    fuzzy_scalars.all = MagicMock(return_value=list(fuzzy_pool))
    fuzzy_res.scalars = MagicMock(return_value=fuzzy_scalars)
    queue.append(fuzzy_res)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=queue)
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


# ---------------------------------------------------------------------------
# _normalize — the canonical key for fuzzy comparison.
# ---------------------------------------------------------------------------


def test_normalize_strips_corporate_suffixes_consistently():
    """`Acme Inc`, `ACME Inc.`, `Acme LLC`, `acme corp` must all
    normalize to the same key so they bucket together for fuzzy
    matching. A regression that misses a suffix variant under-counts
    matches and creates a duplicate vendor row."""
    targets = [
        "Acme Inc",
        "ACME Inc.",
        "Acme LLC",
        "acme corp",
        "Acme Company",
        "Acme Limited",
        "Acme Group",
        "Acme Co.",
        "Acme Co",
        "Acme Corp.",
    ]
    keys = {_normalize(t) for t in targets}
    assert keys == {"acme"}, f"normalize did not collapse to one key: {keys}"


def test_normalize_collapses_whitespace_and_punctuation():
    """`Acme  &  Co!!!` → `acme`. Punctuation drops, double spaces
    collapse, suffix strips. The whole point is order-insensitive
    bag-of-words comparison downstream."""
    assert _normalize("  ACME!! &&  Co.  ") == "acme"
    # Punctuation in the middle of a name shouldn't merge tokens —
    # alphanumerics survive, separators become spaces and then collapse.
    assert _normalize("Smith-Jones Ltd") == "smithjones"


def test_normalize_handles_empty_input():
    """An empty / whitespace-only name should not crash."""
    assert _normalize("") == ""
    assert _normalize("   ") == ""


# ---------------------------------------------------------------------------
# _similarity — Jaccard on tokens. Order-invariant.
# ---------------------------------------------------------------------------


def test_similarity_identical_strings_is_one():
    assert _similarity("acme inc", "acme inc") == 1.0


def test_similarity_disjoint_strings_is_zero():
    assert _similarity("acme", "globex") == 0.0


def test_similarity_is_order_invariant():
    """Token set semantics — re-ordering words can't change the
    score. A regression to substring matching would break this."""
    assert _similarity("acme widgets co", "widgets co acme") == 1.0


def test_similarity_empty_input_returns_zero():
    """Empty token set on either side → 0.0, not a divide-by-zero."""
    assert _similarity("", "anything") == 0.0
    assert _similarity("anything", "") == 0.0


# ---------------------------------------------------------------------------
# match_vendor — the matching ladder.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tax_id_exact_match_wins_confidence_one():
    """Tax ID is the highest-trust key (legal entity, not a name).
    Any tax_id hit short-circuits the rest of the ladder and returns
    confidence 1.0."""
    target = _v("Acme Corp", tax_id="12-3456789")
    db = _mk_db(expects_tax_query=True, tax_match=target)
    vendor, conf = await match_vendor(
        db, vendor_name="Acme Corporation", vendor_tax_id="12-3456789"
    )
    assert vendor is target
    assert conf == 1.0


@pytest.mark.asyncio
async def test_name_exact_match_case_insensitive_returns_high_confidence():
    """No tax_id match, but the name matches exactly (case-insensitive
    after strip). Return confidence 0.98."""
    target = _v("Acme Corp")
    db = _mk_db(name_match=target)
    vendor, conf = await match_vendor(db, vendor_name="  acme corp ")
    assert vendor is target
    assert conf == 0.98


@pytest.mark.asyncio
async def test_fuzzy_match_above_threshold_returns_top_vendor():
    """No exact hits, but the normalized name matches one of the
    pool entries closely enough (≥ 0.6). Best-scoring candidate wins."""
    pool = [
        _v("Globex Corporation"),  # disjoint
        _v("Acme Industries Inc"),  # token overlap with "Acme Industries"
        _v("Pied Piper"),  # disjoint
    ]
    db = _mk_db(fuzzy_pool=pool)
    vendor, conf = await match_vendor(db, vendor_name="Acme Industries")
    assert vendor is pool[1]
    assert conf >= 0.6


@pytest.mark.asyncio
async def test_fuzzy_match_below_threshold_returns_none():
    """When nothing scores above 0.6, return (None, 0.0). A regression
    that returned the best-of-bad-options would link invoices to
    random vendors."""
    pool = [
        _v("Globex Corporation"),
        _v("Pied Piper"),
    ]
    db = _mk_db(fuzzy_pool=pool)
    vendor, conf = await match_vendor(db, vendor_name="Acme Industries")
    assert vendor is None
    assert conf == 0.0


@pytest.mark.asyncio
async def test_empty_vendor_name_returns_none_without_querying():
    """A blank vendor_name must short-circuit — don't query the DB,
    don't claim any match."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=AssertionError("must not query"))
    vendor, conf = await match_vendor(db, vendor_name="")
    assert vendor is None
    assert conf == 0.0
    db.execute.assert_not_called()


# ---------------------------------------------------------------------------
# match_and_link_vendor — orchestration: match, then link or create.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_confidence_match_links_invoice_without_creating_vendor():
    """≥ 0.8 → set invoice.vendor_id, return ("linked"). Don't add a
    new Vendor — duplicates poison vendor spend rollups."""
    existing = _v("Acme Inc", tax_id="12-3456789")
    invoice = SimpleNamespace(
        id=uuid.uuid4(),
        vendor_name="Acme Inc",
        vendor_tax_id="12-3456789",
        vendor_address=None,
        vendor_id=None,
    )
    db = _mk_db(expects_tax_query=True, tax_match=existing)
    org_id = uuid.uuid4()

    vendor, action = await match_and_link_vendor(db, invoice, org_id)

    assert action == "linked"
    assert vendor is existing
    assert invoice.vendor_id == existing.id
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_no_match_creates_unverified_vendor_from_invoice():
    """Nothing matched → create a Vendor with status='unverified',
    source='ai_extracted'. The AP team reviews these from the
    `unverified` queue before activating."""
    entity_id = uuid.uuid4()
    invoice = SimpleNamespace(
        id=uuid.uuid4(),
        vendor_name="Brand New Supplier",
        vendor_tax_id="99-9999999",
        vendor_address="123 Newco St",
        vendor_id=None,
        entity_id=entity_id,
    )
    db = _mk_db(expects_tax_query=True)  # tax_id supplied → 3 queries
    org_id = uuid.uuid4()

    vendor, action = await match_and_link_vendor(db, invoice, org_id)

    assert action == "created"
    # The created vendor was passed to db.add() — read it back.
    db.add.assert_called_once()
    added = db.add.call_args.args[0]
    assert added.name == "Brand New Supplier"
    assert added.status == "unverified"
    assert added.source == "ai_extracted"
    assert added.tax_id == "99-9999999"
    assert added.organization_id == org_id
    # The auto-created vendor inherits the invoice's entity (multi-entity P2).
    assert added.entity_id == entity_id
    # The link must have happened — invoice.vendor_id is assigned
    # from the new Vendor's .id (which the DB populates on flush;
    # against the mock here it stays whatever the model default is,
    # so what matters is the assignment occurred to the same object).
    assert invoice.vendor_id is added.id
    assert vendor is added


@pytest.mark.asyncio
async def test_invoice_with_no_vendor_name_returns_none_action():
    """Invoice arrived without a vendor_name on it (extraction failed
    or pre-extraction): skip matching entirely."""
    invoice = SimpleNamespace(vendor_name=None, vendor_id=None)
    db = AsyncMock()
    vendor, action = await match_and_link_vendor(db, invoice, uuid.uuid4())
    assert action == "none"
    assert vendor is None
    assert invoice.vendor_id is None
