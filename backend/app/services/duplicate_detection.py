"""Semantic duplicate detection via pgvector.

Complements the rule-based duplicate check in `invoice_warnings.py` (which
matches on exact `vendor_name + invoice_number`). Catches near-duplicates
where text overlap is very high but strings differ slightly — re-uploads,
resends from the same vendor with one field changed, OCR-whitespace drift.

The threshold (AP_DUPLICATE_SIMILARITY_THRESHOLD, default 0.95) is
intentionally tighter than the RAG-retrieval threshold. RAG wants
semantically-related-but-distinct invoices for few-shot context;
duplicate detection wants near-identical invoices. A recurring monthly
invoice from the same vendor with a new amount/date typically lands in
the 0.85-0.93 range and should NOT be flagged.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.invoice_embedding import InvoiceEmbedding
from app.services.embedding_adapters import get_embedding_adapter

logger = logging.getLogger(__name__)

# Upper bound on candidates returned. Near-duplicates are rare; anything
# past a handful usually means the threshold is wrong.
MAX_CANDIDATES = 10


@dataclass
class DuplicateMatch:
    invoice_id: uuid.UUID
    similarity: float
    vendor_name: str | None
    invoice_number: str | None
    amount: str | None


async def find_semantic_duplicates(
    db: AsyncSession,
    invoice_text: str,
    *,
    exclude_invoice_id: uuid.UUID | None = None,
    threshold: float | None = None,
) -> list[DuplicateMatch]:
    """Find invoices whose stored embedding is near-identical to the query.

    Returns [] when:
      - RAG is disabled
      - text is empty (scanned PDFs without a text layer)
      - no stored embeddings pass the threshold
    """
    if not settings.rag_enabled or not invoice_text:
        return []

    threshold = threshold if threshold is not None else settings.duplicate_similarity_threshold

    adapter = get_embedding_adapter()
    query = await adapter.embed(invoice_text)

    distance_col = InvoiceEmbedding.embedding.cosine_distance(query.vector).label("distance")
    stmt = select(
        InvoiceEmbedding.invoice_id,
        InvoiceEmbedding.corrected_fields,
        distance_col,
    )
    if exclude_invoice_id is not None:
        stmt = stmt.where(InvoiceEmbedding.invoice_id != exclude_invoice_id)
    stmt = stmt.order_by(distance_col).limit(MAX_CANDIDATES)

    rows = (await db.execute(stmt)).all()

    matches: list[DuplicateMatch] = []
    for invoice_id, corrected, dist in rows:
        similarity = max(0.0, 1.0 - float(dist))
        if similarity < threshold:
            # Rows are sorted ascending by distance (= descending by
            # similarity). Once we drop below the threshold, nothing later
            # in the list will match either.
            break
        fields = corrected or {}
        matches.append(
            DuplicateMatch(
                invoice_id=invoice_id,
                similarity=similarity,
                vendor_name=fields.get("vendor_name"),
                invoice_number=fields.get("invoice_number"),
                amount=fields.get("amount"),
            )
        )

    if matches:
        logger.info(
            "Duplicate detection: %d near-matches for new invoice (threshold %.2f)",
            len(matches),
            threshold,
        )
    return matches


def matches_to_warning(matches: list[DuplicateMatch]) -> dict[str, Any] | None:
    """Convert a list of DuplicateMatch to an invoice.warnings entry."""
    if not matches:
        return None
    top = matches[0]
    return {
        "type": "duplicate_similar",
        "severity": "warning",
        "message": (
            f"Potential duplicate: {top.similarity:.0%} match to "
            f"{top.invoice_number or 'another invoice'}"
            + (f" from {top.vendor_name}" if top.vendor_name else "")
        ),
        "related_invoices": [
            {
                "invoice_id": str(m.invoice_id),
                "invoice_number": m.invoice_number,
                "vendor_name": m.vendor_name,
                "amount": m.amount,
                "similarity": round(m.similarity, 4),
            }
            for m in matches
        ],
    }
