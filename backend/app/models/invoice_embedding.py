"""RAG embeddings for approved invoices.

One row per invoice whose correct values have been confirmed by a reviewer.
When a new invoice is extracted, we embed its text, find the top-k nearest
rows here via cosine distance, and feed the matched pairs to the extraction
adapter as few-shot examples.

See backend/docs/ai-extraction.md § RAG with pgvector.

Tenant-scoped — lives in each `ap_<slug>` database alongside invoices and
vendors. The `vector` column type is provided by the pgvector Postgres
extension (created by services.tenant_provisioning._create_tenant_tables).
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Must match AP_EMBEDDING_DIMENSIONS. Changing requires re-embedding everything.
EMBEDDING_DIMENSIONS = 1536


class InvoiceEmbedding(Base, TimestampMixin):
    __tablename__ = "invoice_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id"), index=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Snapshot of the final (corrected) extracted fields — this is what we
    # feed the AI as the "right answer" for similar future invoices.
    corrected_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Name of the embedding model that produced the vector. Rows from
    # different models are not directly comparable — purge + re-embed on change.
    model: Mapped[str] = mapped_column(default="", nullable=False)
