"""Post-extraction validation of AI-suggested GL codes against the
org's active chart of accounts.

The AI is constrained by the prompt (see `extraction_adapters/claude_vision.py`
where `{{GL_ACCOUNT_CATALOG}}` is replaced with the active chart),
but it can still hallucinate a plausible-looking code. These tests
lock the post-extraction guard:

  - Hallucinated invoice-level GL is dropped + warning emitted
  - Hallucinated line-item GL is dropped (line still persists)
  - Multiple bad codes deduplicate into a single aggregated warning
  - When the org hasn't synced any chart, validation no-ops (we
    can't validate without a reference)
  - A vendor prior whose value is no longer in the active chart is
    cleared after `apply_priors_to_invoice` overlays it
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.extraction_adapters.base import (
    ExtractedField,
    ExtractedLineItem,
    ExtractionResult,
)

# ---------- Fixtures (mirrors test_extraction_usage_placement patterns) -----------


def _make_invoice():
    """Minimal Invoice stand-in with every attr run_extraction touches."""
    from app.models.invoice import InvoiceStatus

    return SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        file_key="invoices/test.pdf",
        status=InvoiceStatus("pending"),
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
        entity_id=None,
        warnings=None,
        po_match=None,
    )


def _make_gl(code: str, name: str = "Test", active: bool = True):
    return SimpleNamespace(code=code, name=name, account_type="expense", is_active=active)


def _make_db(active_codes: list[str]):
    """Build a mock tenant DB whose first execute() returns the GL list.

    `run_extraction` queries GLAccount very early; later execute() calls
    are catchalls that don't drive assertions in these tests, so we
    return a chainable stub for them.

    `db.add` is a plain MagicMock (not AsyncMock) because SQLAlchemy's
    `Session.add` is synchronous — without this, `db.add(...)` calls
    in the production path leak unawaited-coroutine RuntimeWarnings.
    """
    db = AsyncMock()
    db.add = MagicMock()
    gl_objs = [_make_gl(c) for c in active_codes]

    gl_scalars = MagicMock()
    gl_scalars.all = MagicMock(return_value=gl_objs)
    gl_result = MagicMock()
    gl_result.scalars = MagicMock(return_value=gl_scalars)
    gl_result.scalar_one_or_none = MagicMock(return_value=None)

    # First call returns the GL accounts; every subsequent call returns a
    # generic empty stub so post-extraction db.execute()s don't blow up.
    generic = MagicMock()
    generic.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    generic.scalar_one_or_none = MagicMock(return_value=None)

    db.execute = AsyncMock(side_effect=[gl_result] + [generic] * 50)
    return db


def _result_with_gl(
    *, suggested_gl: str | None, suggested_gl_conf: float, line_gls: list[str | None]
) -> ExtractionResult:
    """Build an ExtractionResult with the GL fields we care about."""
    line_items = []
    for idx, gl in enumerate(line_gls):
        line_items.append(
            ExtractedLineItem(
                line_number=idx + 1,
                description=ExtractedField(value=f"Item {idx + 1}", confidence=0.9),
                quantity=ExtractedField(value="1", confidence=0.9),
                unit_price=ExtractedField(value="100.00", confidence=0.9),
                total=ExtractedField(value="100.00", confidence=0.9),
                gl_account=ExtractedField(value=gl, confidence=0.85),
            )
        )

    return ExtractionResult(
        success=True,
        overall_confidence=0.92,
        vendor_name=ExtractedField(value="Acme Corp", confidence=0.95),
        invoice_number=ExtractedField(value="INV-001", confidence=0.99),
        amount=ExtractedField(value="1000.00", confidence=0.97),
        suggested_gl_account=ExtractedField(value=suggested_gl, confidence=suggested_gl_conf),
        line_items=line_items,
        provider="mock",
        raw_response={},
    )


def _patch_internals(extraction_result: ExtractionResult, *, applied_priors: list[str] = None):
    """Mock everything `run_extraction` reaches for, like
    test_extraction_usage_placement does. Returns an ExitStack."""
    stack = ExitStack()

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"PDF")}
    stack.enter_context(patch("boto3.client", return_value=mock_s3))

    mock_adapter = MagicMock()
    mock_adapter.provider_name = "mock"
    mock_adapter.extract = AsyncMock(return_value=extraction_result)
    stack.enter_context(
        patch(
            "app.services.extraction_adapters.get_extraction_adapter",
            return_value=mock_adapter,
        )
    )

    stack.enter_context(patch("app.services.rag.extract_invoice_text", return_value=""))
    stack.enter_context(patch("app.services.rag.retrieve_similar", AsyncMock(return_value=[])))
    stack.enter_context(patch("app.services.rag.build_few_shot_prompt", return_value=""))
    stack.enter_context(patch("app.services.rag.neighbors_to_metadata", return_value=[]))

    fake_vendor = SimpleNamespace(id=uuid.uuid4())
    stack.enter_context(
        patch(
            "app.services.vendor_matching.match_and_link_vendor",
            AsyncMock(return_value=(fake_vendor, "matched")),
        )
    )
    stack.enter_context(
        patch(
            "app.services.vendor_priors.apply_priors_to_invoice",
            AsyncMock(return_value=applied_priors or []),
        )
    )
    stack.enter_context(
        patch(
            "app.services.duplicate_detection.find_semantic_duplicates",
            AsyncMock(return_value=[]),
        )
    )
    stack.enter_context(
        patch("app.services.duplicate_detection.matches_to_warning", return_value=None)
    )
    stack.enter_context(patch("app.services.invoice_warnings.refresh_warnings", AsyncMock()))
    stack.enter_context(patch("app.services.extraction.transition_invoice", AsyncMock()))
    stack.enter_context(
        patch("app.services.extraction.get_workflow_instance", AsyncMock(return_value=None))
    )
    stack.enter_context(patch("app.services.extraction.advance_workflow", AsyncMock()))

    return stack


def _gl_warnings(invoice) -> list[dict]:
    return [w for w in (invoice.warnings or []) if w.get("type") == "gl_account_invalid"]


# ---------- Invoice-level validation -------------------------------------


@pytest.mark.asyncio
async def test_invoice_gl_dropped_when_not_in_active_chart():
    """Most important case: AI hallucinates a code (9999) that doesn't
    exist in the org's chart. The suggestion must NOT land on
    invoice.gl_account, and a structured warning must be appended."""
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db(active_codes=["6100", "6200", "6300"])

    result = _result_with_gl(suggested_gl="9999", suggested_gl_conf=0.9, line_gls=[])
    with _patch_internals(result):
        await run_extraction(db, invoice, actor_id=uuid.uuid4())

    assert invoice.gl_account is None
    warnings = _gl_warnings(invoice)
    assert len(warnings) == 1
    assert warnings[0]["severity"] == "warning"
    assert "9999" in warnings[0]["codes"]
    assert "9999" in warnings[0]["message"]


@pytest.mark.asyncio
async def test_invoice_gl_kept_when_in_active_chart():
    """The happy path — a valid suggestion lands on the invoice and no
    GL warning is emitted."""
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db(active_codes=["6100", "6200", "6300"])

    result = _result_with_gl(suggested_gl="6200", suggested_gl_conf=0.9, line_gls=[])
    with _patch_internals(result):
        await run_extraction(db, invoice, actor_id=uuid.uuid4())

    assert invoice.gl_account == "6200"
    assert _gl_warnings(invoice) == []


@pytest.mark.asyncio
async def test_invoice_gl_validation_skipped_when_no_chart_synced():
    """If the org hasn't loaded a chart of accounts yet, there's nothing
    to validate against. The AI suggestion is accepted as-is so a brand-
    new tenant doesn't get every invoice flagged before they sync."""
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db(active_codes=[])  # empty chart

    result = _result_with_gl(suggested_gl="9999", suggested_gl_conf=0.9, line_gls=[])
    with _patch_internals(result):
        await run_extraction(db, invoice, actor_id=uuid.uuid4())

    assert invoice.gl_account == "9999"
    assert _gl_warnings(invoice) == []


@pytest.mark.asyncio
async def test_invoice_gl_low_confidence_not_applied_or_validated():
    """Confidence below 0.7 means the AI itself isn't sure — we leave
    the field blank for the reviewer rather than asserting + warning.
    Also verifies the validator doesn't fire on a value we never
    actually set."""
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db(active_codes=["6100"])

    result = _result_with_gl(suggested_gl="9999", suggested_gl_conf=0.5, line_gls=[])
    with _patch_internals(result):
        await run_extraction(db, invoice, actor_id=uuid.uuid4())

    assert invoice.gl_account is None
    assert _gl_warnings(invoice) == []


# ---------- Line-item validation -----------------------------------------


@pytest.mark.asyncio
async def test_line_item_gl_dropped_when_not_in_active_chart():
    """Invalid GL on a line is dropped to None (the line itself still
    persists) and the offending code shows up in the aggregate warning."""
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db(active_codes=["6100"])
    captured_lines: list = []

    def capture_add(obj):
        captured_lines.append(obj)

    db.add = MagicMock(side_effect=capture_add)

    result = _result_with_gl(
        suggested_gl=None, suggested_gl_conf=0.0, line_gls=["6100", "9999", None]
    )
    with _patch_internals(result):
        await run_extraction(db, invoice, actor_id=uuid.uuid4())

    # Three line items added — one valid, one invalid (gl wiped), one with no gl.
    line_items = [obj for obj in captured_lines if hasattr(obj, "line_number")]
    assert len(line_items) == 3
    by_num = {li.line_number: li for li in line_items}
    assert by_num[1].gl_account == "6100"
    assert by_num[2].gl_account is None  # 9999 stripped
    assert by_num[3].gl_account is None

    warnings = _gl_warnings(invoice)
    assert len(warnings) == 1
    assert warnings[0]["codes"] == ["9999"]


@pytest.mark.asyncio
async def test_multiple_invalid_codes_dedup_into_single_warning():
    """An invoice with the same hallucinated code on header and line
    should emit ONE aggregate warning, not two — the reviewer reads it
    once and acts once."""
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db(active_codes=["6100"])

    result = _result_with_gl(suggested_gl="9999", suggested_gl_conf=0.9, line_gls=["9999", "8888"])
    with _patch_internals(result):
        await run_extraction(db, invoice, actor_id=uuid.uuid4())

    warnings = _gl_warnings(invoice)
    assert len(warnings) == 1
    # Codes deduplicated and sorted (sorted set then back to list).
    assert warnings[0]["codes"] == ["8888", "9999"]


# ---------- Stale-prior overlay -------------------------------------------


@pytest.mark.asyncio
async def test_stale_vendor_prior_gl_cleared_with_warning():
    """`apply_priors_to_invoice` overlays a cached gl_account when AI
    confidence is low. If the cache is now stale (the code was
    deactivated since it was learned), the post-priors guard must
    clear it and warn — otherwise the stale code rides through to
    the ERP push."""
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db(active_codes=["6100", "6200"])

    # Simulate the priors overlay setting invoice.gl_account = "5500"
    # (a code that's no longer in the active chart).
    async def fake_apply_priors(db_, inv_, result_):
        inv_.gl_account = "5500"
        return ["gl_account"]

    result = _result_with_gl(suggested_gl=None, suggested_gl_conf=0.0, line_gls=[])
    with _patch_internals(result):
        with patch(
            "app.services.vendor_priors.apply_priors_to_invoice",
            AsyncMock(side_effect=fake_apply_priors),
        ):
            await run_extraction(db, invoice, actor_id=uuid.uuid4())

    assert invoice.gl_account is None
    warnings = _gl_warnings(invoice)
    assert len(warnings) == 1
    assert warnings[0]["codes"] == ["5500"]
    msg = warnings[0]["message"].lower()
    assert "stale" in msg or "no longer" in msg


@pytest.mark.asyncio
async def test_fresh_vendor_prior_gl_kept_when_still_in_chart():
    """Sanity check: a cached prior whose code IS still active should
    survive the post-priors guard untouched. Otherwise we'd clobber
    every prior on every extraction."""
    from app.services.extraction import run_extraction

    invoice = _make_invoice()
    db = _make_db(active_codes=["6100", "6200"])

    async def fake_apply_priors(db_, inv_, result_):
        inv_.gl_account = "6200"
        return ["gl_account"]

    result = _result_with_gl(suggested_gl=None, suggested_gl_conf=0.0, line_gls=[])
    with _patch_internals(result):
        with patch(
            "app.services.vendor_priors.apply_priors_to_invoice",
            AsyncMock(side_effect=fake_apply_priors),
        ):
            await run_extraction(db, invoice, actor_id=uuid.uuid4())

    assert invoice.gl_account == "6200"
    assert _gl_warnings(invoice) == []
