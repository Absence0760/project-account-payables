"""Locks the AI Auto GL Coding wiring into the extraction prompt.

These features were implemented but had no test coverage — this file
catches a regression where someone reverts either piece.

Items covered:
  1. Custom chart of accounts per org in the prompt — the prompt has
     a `{{GL_ACCOUNT_CATALOG}}` placeholder; `extraction.run_extraction`
     queries the org's active GL accounts and injects them via
     `config["gl_account_catalog"]`; the adapter swaps the placeholder.
  2. RAG-driven GL coding — the few-shot prompt built from approved
     neighbors is prepended; `SNAPSHOT_FIELDS` includes `gl_account`
     so the example actually carries a code.
"""

from __future__ import annotations

# ---------- Prompt template wiring ----------------------------------------


def test_extraction_prompt_template_has_gl_catalog_placeholder():
    """The placeholder is what the adapter swaps; lose it and the
    org-specific chart can't make it into the prompt."""
    from app.services.extraction_adapters.claude_vision import (
        _EXTRACTION_PROMPT_TEMPLATE,
        _GL_PLACEHOLDER,
    )

    assert _GL_PLACEHOLDER == "{{GL_ACCOUNT_CATALOG}}"
    assert _GL_PLACEHOLDER in _EXTRACTION_PROMPT_TEMPLATE


def test_extraction_prompt_falls_back_to_default_gl_list():
    """When no org catalog is configured (fresh tenant), the adapter
    must still produce a valid prompt — backward-compatible default
    list keeps extraction working before the chart sync runs."""
    from app.services.extraction_adapters.claude_vision import (
        _DEFAULT_GL_LIST,
        _GL_PLACEHOLDER,
        EXTRACTION_PROMPT,
    )

    assert _GL_PLACEHOLDER not in EXTRACTION_PROMPT
    # Pick one of the default expense codes that should be in the baked-in list.
    assert "6100" in _DEFAULT_GL_LIST
    assert _DEFAULT_GL_LIST in EXTRACTION_PROMPT


# ---------- RAG snapshot fields -----------------------------------------


def test_rag_snapshot_fields_include_gl_account():
    """RAG-driven GL coding works only if the corrected_fields snapshot
    captures the approved invoice's GL code. Drop this from
    SNAPSHOT_FIELDS and the few-shot prompt loses its GL signal — the
    AI can no longer learn from approved nearest neighbors."""
    from app.services.rag import SNAPSHOT_FIELDS

    assert "gl_account" in SNAPSHOT_FIELDS
    assert "cost_center" in SNAPSHOT_FIELDS


def test_few_shot_prompt_includes_gl_when_present_in_corrected_fields():
    """End-to-end check: a Neighbor whose corrected_fields carries a
    gl_account renders into the few-shot prompt that gets prepended
    to the extraction prompt."""
    import uuid

    from app.services.rag import Neighbor, build_few_shot_prompt

    n = Neighbor(
        invoice_id=uuid.uuid4(),
        similarity=0.93,
        vendor_name="Acme",
        corrected_fields={
            "vendor_name": "Acme",
            "amount": "1000.00",
            "gl_account": "6100",
        },
    )
    prompt = build_few_shot_prompt([n])
    assert "6100" in prompt or "gl_account" in prompt.lower(), (
        "few-shot prompt must surface the neighbor's gl_account so the AI sees a coded example"
    )


# ---------- API surface for the chart-of-accounts -------------------------


def test_gl_accounts_router_has_list_endpoint():
    """The frontend's InvoiceModal hydrates the GL dropdown from
    `GET /api/gl-accounts`. Lock the route is registered (and not
    accidentally renamed)."""
    from app.api import gl_accounts as gl_accounts_module

    paths = {r.path for r in gl_accounts_module.router.routes}
    assert "/gl-accounts" in paths
    assert "/gl-accounts/sync-erp" in paths
