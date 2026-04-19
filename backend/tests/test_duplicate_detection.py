"""Unit tests for semantic duplicate detection.

Pure-function tests — exercises matches_to_warning contract and the
DuplicateMatch dataclass. The find_semantic_duplicates DB query is
integration-tested indirectly via the RAG store tests.
"""

import uuid


def test_matches_to_warning_none_when_empty():
    from app.services.duplicate_detection import matches_to_warning

    assert matches_to_warning([]) is None


def test_matches_to_warning_contract():
    """Lock the shape the invoice-list UI + exception queue depend on."""
    from app.services.duplicate_detection import DuplicateMatch, matches_to_warning

    matches = [
        DuplicateMatch(
            invoice_id=uuid.uuid4(),
            similarity=0.9823,
            vendor_name="Northwind Suppliers Ltd",
            invoice_number="INV-2026-00418",
            amount="12480.00",
        ),
        DuplicateMatch(
            invoice_id=uuid.uuid4(),
            similarity=0.9541,
            vendor_name="Northwind Suppliers Ltd",
            invoice_number="INV-2026-00401",
            amount="12480.00",
        ),
    ]
    warning = matches_to_warning(matches)
    assert warning["type"] == "duplicate_similar"
    assert warning["severity"] == "warning"
    # Message surfaces the top match
    assert "98%" in warning["message"]
    assert "INV-2026-00418" in warning["message"]
    assert "Northwind" in warning["message"]

    # All matches preserved in related_invoices for UI display
    assert len(warning["related_invoices"]) == 2
    ri = warning["related_invoices"][0]
    assert set(ri.keys()) == {
        "invoice_id",
        "invoice_number",
        "vendor_name",
        "amount",
        "similarity",
    }
    assert ri["similarity"] == 0.9823  # 4-decimal rounding preserves precision


def test_matches_to_warning_handles_missing_fields():
    """Embeddings without corrected_fields (unlikely but possible) don't crash."""
    from app.services.duplicate_detection import DuplicateMatch, matches_to_warning

    m = DuplicateMatch(
        invoice_id=uuid.uuid4(),
        similarity=0.96,
        vendor_name=None,
        invoice_number=None,
        amount=None,
    )
    warning = matches_to_warning([m])
    # Message falls back gracefully
    assert "another invoice" in warning["message"]
    assert warning["related_invoices"][0]["vendor_name"] is None


def test_duplicate_threshold_tighter_than_rag_top_k_default():
    """Duplicate detection MUST be stricter than RAG retrieval — otherwise
    every RAG neighbor fires a duplicate warning, which defeats both."""
    from app.config import settings

    assert settings.duplicate_similarity_threshold >= 0.9


def test_max_candidates_bounded():
    """Guard against pathological cases where the whole table could come back."""
    from app.services.duplicate_detection import MAX_CANDIDATES

    assert 1 <= MAX_CANDIDATES <= 20
