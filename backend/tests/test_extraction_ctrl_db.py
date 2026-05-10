"""Tests that run_extraction uses ctrl_db correctly for ExtractionUsage.

ExtractionUsage lives in the control-plane DB, not the tenant DB. These
tests confirm that:
  1. When ctrl_db is provided, ExtractionUsage is added to ctrl_db (not db).
  2. When ctrl_db is None, ExtractionUsage tracking is silently skipped.
  3. On the failure path, the same ctrl_db vs None semantics apply.

We mock everything that touches the network or DB — S3, the extraction
adapter, RAG, vendor matching, vendor priors, duplicate detection,
invoice_warnings, and workflow_engine — so the tests are fully hermetic.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.extraction_adapters.base import ExtractedField, ExtractionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_invoice(*, status_value="pending"):
    """Minimal Invoice stand-in with all attrs run_extraction touches."""
    from app.models.invoice import InvoiceStatus

    status = InvoiceStatus(status_value)
    return SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        file_key="invoices/test.pdf",
        status=status,
        amount=None,
        vendor_name="Acme Corp",
        invoice_number="INV-001",
        invoice_date=None,
        due_date=None,
        payment_terms=None,
        payment_method=None,
        po_number=None,
        description=None,
        vendor_address=None,
        vendor_tax_id=None,
        reference_number=None,
        bill_to_address=None,
        remit_to_address=None,
        subtotal=None,
        tax_amount=None,
        tax_rate=None,
        discount_amount=None,
        shipping_amount=None,
        currency="USD",
        gl_account=None,
        cost_center=None,
        vendor_id=None,
        warnings=None,
        po_match=None,
    )


def _make_db(re_fetch_invoice=None):
    """Build a tenant DB AsyncMock whose execute().scalar_one_or_none() returns
    re_fetch_invoice (used in the failure re-fetch path).

    `db.add` is a regular MagicMock (not AsyncMock) because SQLAlchemy's
    `Session.add` is synchronous — leaving it as AsyncMock leaks an
    unawaited-coroutine RuntimeWarning per call.
    """
    db = AsyncMock()
    db.add = MagicMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=re_fetch_invoice)
    db.execute.return_value = execute_result
    return db


def _successful_extraction_result() -> ExtractionResult:
    """A minimal successful ExtractionResult."""
    return ExtractionResult(
        success=True,
        overall_confidence=0.92,
        vendor_name=ExtractedField(value="Acme Corp", confidence=0.95),
        invoice_number=ExtractedField(value="INV-001", confidence=0.99),
        amount=ExtractedField(value="1000.00", confidence=0.97),
        provider="mock",
        raw_response={"raw": True},
    )


def _failing_extraction_result() -> ExtractionResult:
    return ExtractionResult(success=False, error="AI quota exceeded")


def _patch_extraction_internals(extraction_result: ExtractionResult):
    """Return a context-manager stack that mocks every external call in run_extraction.

    run_extraction imports several modules *inside* the function body (lazy
    imports), so we patch at the final module path, not at
    'app.services.extraction.<name>'.
    """
    from contextlib import ExitStack

    stack = ExitStack()

    # S3
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"PDF content")}
    stack.enter_context(patch("boto3.client", return_value=mock_s3))

    # Extraction adapter — patch get_extraction_adapter in the adapters package
    mock_adapter = MagicMock()
    mock_adapter.provider_name = "mock"
    mock_adapter.extract = AsyncMock(return_value=extraction_result)
    stack.enter_context(
        patch(
            "app.services.extraction_adapters.get_extraction_adapter",
            return_value=mock_adapter,
        )
    )

    # RAG — patched at the module level the service imports from
    stack.enter_context(patch("app.services.rag.extract_invoice_text", return_value="invoice text"))
    stack.enter_context(patch("app.services.rag.retrieve_similar", AsyncMock(return_value=[])))
    stack.enter_context(patch("app.services.rag.build_few_shot_prompt", return_value=""))
    stack.enter_context(patch("app.services.rag.neighbors_to_metadata", return_value=[]))

    # Vendor matching — imported inside run_extraction as:
    #   from app.services.vendor_matching import match_and_link_vendor
    fake_vendor = SimpleNamespace(id=uuid.uuid4())
    stack.enter_context(
        patch(
            "app.services.vendor_matching.match_and_link_vendor",
            AsyncMock(return_value=(fake_vendor, "matched")),
        )
    )

    # Vendor priors
    stack.enter_context(
        patch(
            "app.services.vendor_priors.apply_priors_to_invoice",
            AsyncMock(return_value=[]),
        )
    )

    # Duplicate detection
    stack.enter_context(
        patch(
            "app.services.duplicate_detection.find_semantic_duplicates",
            AsyncMock(return_value=[]),
        )
    )
    stack.enter_context(
        patch("app.services.duplicate_detection.matches_to_warning", return_value=None)
    )

    # Invoice warnings
    stack.enter_context(patch("app.services.invoice_warnings.refresh_warnings", AsyncMock()))

    # Workflow engine — these are imported at the top of extraction.py so
    # patch via the extraction module's namespace.
    stack.enter_context(patch("app.services.extraction.transition_invoice", AsyncMock()))
    stack.enter_context(
        patch("app.services.extraction.get_workflow_instance", AsyncMock(return_value=None))
    )
    stack.enter_context(patch("app.services.extraction.advance_workflow", AsyncMock()))

    return stack


# ---------------------------------------------------------------------------
# Success path: ctrl_db provided
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_added_to_ctrl_db_on_success():
    """When extraction succeeds and ctrl_db is provided, ExtractionUsage is
    added to ctrl_db and ctrl_db.commit() is called."""
    from app.models.usage import ExtractionUsage
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db()
    ctrl_db = AsyncMock()
    ctrl_db.add = MagicMock()

    with _patch_extraction_internals(_successful_extraction_result()):
        await run_extraction(
            db,
            invoice,
            actor_id=uuid.uuid4(),
            org_settings=None,
            ctrl_db=ctrl_db,
        )

    ctrl_add_calls = ctrl_db.add.call_args_list
    usage_calls = [c for c in ctrl_add_calls if isinstance(c.args[0], ExtractionUsage)]
    assert len(usage_calls) == 1, "Expected exactly one ExtractionUsage added to ctrl_db"

    usage: ExtractionUsage = usage_calls[0].args[0]
    assert usage.invoice_id == invoice.id
    assert usage.success is True

    ctrl_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_usage_not_added_to_tenant_db_on_success():
    """ExtractionUsage must never be added to the tenant db session."""
    from app.models.usage import ExtractionUsage
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db()
    ctrl_db = AsyncMock()
    ctrl_db.add = MagicMock()

    with _patch_extraction_internals(_successful_extraction_result()):
        await run_extraction(db, invoice, ctrl_db=ctrl_db)

    for c in db.add.call_args_list:
        assert not isinstance(c.args[0], ExtractionUsage), (
            "ExtractionUsage must go to ctrl_db, not the tenant db"
        )


@pytest.mark.asyncio
async def test_usage_contains_correct_fields_on_success():
    """The ExtractionUsage row fields must be plausible billing metadata."""
    from app.models.usage import ExtractionUsage
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db()
    ctrl_db = AsyncMock()
    ctrl_db.add = MagicMock()

    with _patch_extraction_internals(_successful_extraction_result()):
        await run_extraction(db, invoice, actor_id=uuid.uuid4(), ctrl_db=ctrl_db)

    usage = next(
        c.args[0] for c in ctrl_db.add.call_args_list if isinstance(c.args[0], ExtractionUsage)
    )
    assert usage.invoice_id == invoice.id
    assert usage.organization_id == invoice.organization_id
    assert usage.success is True
    # period is "YYYY-MM"
    assert len(usage.period) == 7
    assert usage.period[4] == "-"


# ---------------------------------------------------------------------------
# Success path: ctrl_db is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_silently_skipped_when_ctrl_db_is_none():
    """When ctrl_db=None, no ExtractionUsage is created and no error is raised."""
    from app.models.usage import ExtractionUsage
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db()

    with _patch_extraction_internals(_successful_extraction_result()):
        await run_extraction(db, invoice, ctrl_db=None)

    for c in db.add.call_args_list:
        assert not isinstance(c.args[0], ExtractionUsage)


# ---------------------------------------------------------------------------
# Failure path: ctrl_db provided
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_usage_added_to_ctrl_db_on_extraction_failure():
    """On adapter failure, a failed ExtractionUsage row is added to ctrl_db."""
    from app.models.usage import ExtractionUsage
    from app.services.extraction import run_extraction

    invoice = _make_invoice()

    # After rollback the service re-fetches the invoice via db.execute
    re_fetched = _make_invoice()
    re_fetched.id = invoice.id
    re_fetched.organization_id = invoice.organization_id

    db = _make_db(re_fetch_invoice=re_fetched)
    ctrl_db = AsyncMock()
    ctrl_db.add = MagicMock()

    with _patch_extraction_internals(_failing_extraction_result()):
        await run_extraction(db, invoice, ctrl_db=ctrl_db)

    usage_calls = [c for c in ctrl_db.add.call_args_list if isinstance(c.args[0], ExtractionUsage)]
    assert len(usage_calls) == 1
    usage: ExtractionUsage = usage_calls[0].args[0]
    assert usage.invoice_id == invoice.id
    assert usage.success is False


@pytest.mark.asyncio
async def test_failed_usage_not_added_to_tenant_db_on_failure():
    """On failure, ExtractionUsage must still go to ctrl_db, not the tenant db."""
    from app.models.usage import ExtractionUsage
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    re_fetched = _make_invoice()
    re_fetched.id = invoice.id
    re_fetched.organization_id = invoice.organization_id

    db = _make_db(re_fetch_invoice=re_fetched)
    ctrl_db = AsyncMock()
    ctrl_db.add = MagicMock()

    with _patch_extraction_internals(_failing_extraction_result()):
        await run_extraction(db, invoice, ctrl_db=ctrl_db)

    for c in db.add.call_args_list:
        assert not isinstance(c.args[0], ExtractionUsage), (
            "ExtractionUsage must go to ctrl_db, not the tenant db, even on failure"
        )


# ---------------------------------------------------------------------------
# Failure path: ctrl_db is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_usage_silently_skipped_when_ctrl_db_is_none():
    """When ctrl_db=None and extraction fails, no ExtractionUsage is created
    and run_extraction does not raise."""
    from app.models.usage import ExtractionUsage
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    re_fetched = _make_invoice()
    re_fetched.id = invoice.id
    re_fetched.organization_id = invoice.organization_id

    db = _make_db(re_fetch_invoice=re_fetched)

    with _patch_extraction_internals(_failing_extraction_result()):
        await run_extraction(db, invoice, ctrl_db=None)

    for c in db.add.call_args_list:
        assert not isinstance(c.args[0], ExtractionUsage)


# ---------------------------------------------------------------------------
# Unexpected adapter exception — ctrl_db behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_error_still_writes_failed_usage_to_ctrl_db():
    """When the adapter raises a hard error, the failure handler must still
    record a failed ExtractionUsage on ctrl_db."""
    from app.models.usage import ExtractionUsage
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    re_fetched = _make_invoice()
    re_fetched.id = invoice.id
    re_fetched.organization_id = invoice.organization_id

    db = _make_db(re_fetch_invoice=re_fetched)
    ctrl_db = AsyncMock()
    ctrl_db.add = MagicMock()

    with _patch_extraction_internals(_successful_extraction_result()) as stack:
        # Override the adapter to raise instead of returning a result
        exploding_adapter = MagicMock()
        exploding_adapter.provider_name = "mock"
        exploding_adapter.extract = AsyncMock(side_effect=RuntimeError("OOM"))
        stack.enter_context(
            patch(
                "app.services.extraction_adapters.get_extraction_adapter",
                return_value=exploding_adapter,
            )
        )
        await run_extraction(db, invoice, ctrl_db=ctrl_db)

    usage_calls = [c for c in ctrl_db.add.call_args_list if isinstance(c.args[0], ExtractionUsage)]
    assert len(usage_calls) == 1
    assert usage_calls[0].args[0].success is False
