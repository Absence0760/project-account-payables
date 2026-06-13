"""``find_invoices_by_text`` tool — semantic invoice search.

Wraps ``services.rag.retrieve_similar`` (pgvector cosine search over
``InvoiceEmbedding``; embeddings via the mock embedding adapter by default —
already local-first). Tenant isolation is inherent: ``retrieve_similar`` runs
against the tenant ``db``. Within the tenant, the search is also entity-scoped
(``entity_id`` threaded through to the query) so it honors the selected
subsidiary exactly like every other tool. The snippet is built only from
non-PII corrected fields — never bank / tax values.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.assistant.tools.schemas import (
    TextSearchMatch,
    TextSearchParams,
    TextSearchResult,
)
from app.services.rag import retrieve_similar

# Non-PII fields safe to surface in a snippet. Deliberately excludes
# vendor_tax_id, bank_details, addresses, etc.
_SNIPPET_FIELDS = ("invoice_number", "amount", "currency", "invoice_date", "due_date")


def _build_snippet(corrected_fields: dict) -> str:
    parts = []
    for field in _SNIPPET_FIELDS:
        value = (corrected_fields or {}).get(field)
        if value:
            parts.append(f"{field}: {value}")
    return " · ".join(parts)


async def find_invoices_by_text(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    current_user_id: uuid.UUID,
    params: TextSearchParams,
    control_db: AsyncSession | None = None,
) -> TextSearchResult:
    # Entity-scope the search the same way list_invoices / vendor_spend do, so
    # "search" can't silently widen past the subsidiary the user has selected.
    neighbors = await retrieve_similar(db, params.query, k=params.k, entity_id=entity_id)
    matches = [
        TextSearchMatch(
            invoice_id=str(n.invoice_id),
            vendor_name=n.vendor_name,
            similarity=round(n.similarity, 4),
            snippet=_build_snippet(n.corrected_fields),
        )
        for n in neighbors
    ]
    return TextSearchResult(matches=matches)
