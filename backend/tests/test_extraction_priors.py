"""Unit tests for the extraction priors machinery.

Covers the per-vendor correction cache (vendor_priors) and the RAG store
(rag + embedding adapters + invoice list summary). DB-free — we assert
on whitelists, schema shapes, and deterministic behavior of the mock
embedding adapter.
"""

import asyncio

# ---------- vendor cache whitelist -----------------------------------------


def test_cacheable_fields_only_contain_vendor_consistent_fields():
    """Per-invoice fields must NEVER be cached — stale data would be worse
    than no cache at all. Lock the whitelist to catch accidental additions.
    """
    from app.services.vendor_priors import CACHEABLE_FIELDS

    # These MUST be cacheable (vendor-consistent).
    required = {
        "currency",
        "tax_rate",
        "payment_terms",
        "payment_method",
        "vendor_address",
        "vendor_tax_id",
        "remit_to_address",
        "gl_account",
        "cost_center",
    }
    assert required == CACHEABLE_FIELDS


def test_per_invoice_fields_are_not_cacheable():
    from app.services.vendor_priors import CACHEABLE_FIELDS

    never_cache = {
        "invoice_number",
        "amount",
        "subtotal",
        "tax_amount",
        "discount_amount",
        "shipping_amount",
        "invoice_date",
        "due_date",
        "po_number",
        "reference_number",
        "description",
    }
    overlap = never_cache & CACHEABLE_FIELDS
    assert not overlap, f"Per-invoice fields mustn't be cached: {overlap}"


# ---------- models ---------------------------------------------------------


def test_vendor_extraction_prior_has_required_columns():
    from app.models.vendor_priors import VendorExtractionPrior

    columns = {c.name for c in VendorExtractionPrior.__table__.columns}
    required = {"id", "vendor_id", "field_name", "value", "correction_count", "last_applied_at"}
    assert required.issubset(columns)


def test_vendor_priors_uniqueness_constraint():
    """(vendor_id, field_name) must be unique — the service upserts on it."""
    from app.models.vendor_priors import VendorExtractionPrior

    unique_cols = set()
    for c in VendorExtractionPrior.__table__.constraints:
        if c.__class__.__name__ == "UniqueConstraint":
            unique_cols |= {col.name for col in c.columns}
    assert {"vendor_id", "field_name"}.issubset(unique_cols)


def test_invoice_embedding_has_required_columns():
    from app.models.invoice_embedding import InvoiceEmbedding

    columns = {c.name for c in InvoiceEmbedding.__table__.columns}
    required = {"id", "invoice_id", "vendor_id", "embedding", "corrected_fields", "model"}
    assert required.issubset(columns)


def test_invoice_extraction_result_has_priors_metadata():
    from app.models.invoice import InvoiceExtractionResult

    columns = {c.name for c in InvoiceExtractionResult.__table__.columns}
    assert "priors_metadata" in columns


def test_email_verification_has_required_columns():
    from app.models.signup import EmailVerification

    columns = {c.name for c in EmailVerification.__table__.columns}
    required = {
        "id",
        "token",
        "email",
        "company_name",
        "slug",
        "admin_name",
        "expires_at",
        "consumed_at",
    }
    assert required.issubset(columns)


def test_user_has_must_change_password_column():
    from app.models.user import User

    columns = {c.name for c in User.__table__.columns}
    assert "must_change_password" in columns


# ---------- embedding adapters ---------------------------------------------


def test_mock_embedder_is_deterministic():
    """Same text → same vector. Otherwise RAG retrieval is useless."""
    from app.services.embedding_adapters.mock_adapter import MockEmbeddingAdapter

    adapter = MockEmbeddingAdapter({"dimensions": 1536})
    a = asyncio.run(adapter.embed("hello world"))
    b = asyncio.run(adapter.embed("hello world"))
    assert a.vector == b.vector


def test_mock_embedder_produces_different_vectors_for_different_input():
    from app.services.embedding_adapters.mock_adapter import MockEmbeddingAdapter

    adapter = MockEmbeddingAdapter({"dimensions": 1536})
    a = asyncio.run(adapter.embed("Acme Corp invoice 1"))
    b = asyncio.run(adapter.embed("Globex Corp invoice 1"))
    assert a.vector != b.vector


def test_mock_embedder_returns_unit_length_vector():
    """Unit-normalized so cosine similarity is just dot product."""
    from app.services.embedding_adapters.mock_adapter import MockEmbeddingAdapter

    adapter = MockEmbeddingAdapter({"dimensions": 1536})
    result = asyncio.run(adapter.embed("x"))
    norm_sq = sum(v * v for v in result.vector)
    assert abs(norm_sq - 1.0) < 1e-6


def test_both_embedding_adapters_registered():
    from app.services.embedding_adapters import list_available_providers

    assert {"mock", "openai"}.issubset(set(list_available_providers()))


# ---------- RAG service ----------------------------------------------------


def test_build_few_shot_prompt_empty():
    from app.services.rag import build_few_shot_prompt

    assert build_few_shot_prompt([]) == ""


def test_build_few_shot_prompt_renders_neighbors():
    import uuid as _uuid

    from app.services.rag import Neighbor, build_few_shot_prompt

    n = Neighbor(
        invoice_id=_uuid.uuid4(),
        similarity=0.87,
        vendor_name="Acme",
        corrected_fields={"vendor_name": "Acme", "amount": "1000.00"},
    )
    prompt = build_few_shot_prompt([n])
    assert "similarity 0.87" in prompt
    assert "Acme" in prompt


def test_neighbors_to_metadata_shape():
    """The /api/invoices/{id}/priors endpoint returns this shape — the UI
    depends on these fields. Lock the contract."""
    import uuid as _uuid

    from app.services.rag import Neighbor, neighbors_to_metadata

    n = Neighbor(
        invoice_id=_uuid.uuid4(),
        similarity=0.911234,
        vendor_name="Acme",
        corrected_fields={
            "invoice_number": "INV-1",
            "amount": "99.00",
            "vendor_name": "Acme",
        },
    )
    out = neighbors_to_metadata([n])
    assert len(out) == 1
    required = {"invoice_id", "similarity", "vendor_name", "invoice_number", "amount"}
    assert required.issubset(out[0].keys())
    # similarity rounded to 4 decimal places
    assert out[0]["similarity"] == 0.9112


# ---------- priors summary (invoice list row) ------------------------------


def test_priors_summary_none_when_no_extraction_results():
    from app.schemas.invoice import _priors_summary

    class FakeInvoice:
        extraction_results = []

    assert _priors_summary(FakeInvoice()) is None


def test_priors_summary_counts_both_streams():
    from datetime import datetime

    from app.schemas.invoice import _priors_summary

    class FakeResult:
        created_at = datetime.now()
        priors_metadata = {
            "vendor_cache_applied": ["currency", "tax_rate"],
            "rag_neighbors": [{"invoice_id": "a"}, {"invoice_id": "b"}, {"invoice_id": "c"}],
        }

    class FakeInvoice:
        extraction_results = [FakeResult()]

    summary = _priors_summary(FakeInvoice())
    assert summary == {"cache": 2, "rag": 3}


def test_priors_summary_none_when_both_streams_empty():
    from datetime import datetime

    from app.schemas.invoice import _priors_summary

    class FakeResult:
        created_at = datetime.now()
        priors_metadata = {"vendor_cache_applied": [], "rag_neighbors": []}

    class FakeInvoice:
        extraction_results = [FakeResult()]

    assert _priors_summary(FakeInvoice()) is None


# ---------- email + rate limit modules import cleanly ---------------------


def test_email_adapters_registered():
    from app.services.email_adapters import list_available_providers

    assert {"console", "ses"}.issubset(set(list_available_providers()))
