"""RAG: pgvector + invoice_embeddings + priors_metadata on extraction_results.

Revision ID: 0003_rag
Revises: 0002_vendor_priors
Create Date: 2026-04-19

Tenant-only migration. Enables the pgvector extension, creates the
`invoice_embeddings` table, and adds a `priors_metadata` JSONB column to
`invoice_extraction_results`. Gated on presence of the `invoices` table so
it's a no-op on the control plane DB.

The embedding dimension (1536) must match AP_EMBEDDING_DIMENSIONS.
"""

from alembic import op
from sqlalchemy import text

revision = "0003_rag"
down_revision = "0002_vendor_priors"
branch_labels = None
depends_on = None

EMBEDDING_DIMENSIONS = 1536


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'invoices'"
        )
    ).scalar()
    return result is not None


def upgrade() -> None:
    if not _is_tenant_db():
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS invoice_embeddings (
            id UUID PRIMARY KEY,
            invoice_id UUID NOT NULL UNIQUE REFERENCES invoices(id) ON DELETE CASCADE,
            vendor_id UUID REFERENCES vendors(id),
            embedding vector({EMBEDDING_DIMENSIONS}) NOT NULL,
            corrected_fields JSONB DEFAULT '{{}}'::jsonb,
            model VARCHAR(100) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_invoice_embeddings_vendor_id "
        "ON invoice_embeddings(vendor_id)"
    )
    # HNSW index for fast approximate cosine nearest-neighbor search. Only
    # useful once there are hundreds of rows; on an empty/tiny table it's
    # free overhead.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_invoice_embeddings_embedding_hnsw "
        "ON invoice_embeddings USING hnsw (embedding vector_cosine_ops)"
    )

    op.execute(
        "ALTER TABLE invoice_extraction_results "
        "ADD COLUMN IF NOT EXISTS priors_metadata JSONB"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "ALTER TABLE invoice_extraction_results DROP COLUMN IF EXISTS priors_metadata"
    )
    op.execute("DROP TABLE IF EXISTS invoice_embeddings")
