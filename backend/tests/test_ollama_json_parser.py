"""Tests for `_parse_ollama_json` — the JSON recovery path for noisy
Ollama responses.

Even with `format: "json"`, Llama 3.2 Vision 11B routinely returns output
that fails strict json.loads — fenced blocks, leading prose, trailing
commentary. Without robust extraction the whole extraction silently fails.
"""

from __future__ import annotations

import pytest


def test_direct_parse_happy_path():
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    assert _parse_ollama_json('{"vendor_name": "Acme"}') == {"vendor_name": "Acme"}


def test_returns_none_for_empty_or_garbage():
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    assert _parse_ollama_json("") is None
    assert _parse_ollama_json(None) is None  # type: ignore[arg-type]
    assert _parse_ollama_json("no json here") is None


def test_strips_json_fence():
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    body = '```json\n{"vendor_name": "Acme", "amount": 100}\n```'
    assert _parse_ollama_json(body) == {"vendor_name": "Acme", "amount": 100}


def test_strips_unlabelled_fence():
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    body = '```\n{"vendor_name": "Acme"}\n```'
    assert _parse_ollama_json(body) == {"vendor_name": "Acme"}


def test_extracts_object_from_prose_prefix():
    """11B models love to say "Here is the extraction:" then dump JSON."""
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    body = 'Here is the extracted invoice data:\n{"vendor_name": "Acme", "amount": 100}'
    assert _parse_ollama_json(body) == {"vendor_name": "Acme", "amount": 100}


def test_extracts_object_with_trailing_commentary():
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    body = '{"vendor_name": "Acme"}\n\nHope this helps! Let me know if you need anything else.'
    assert _parse_ollama_json(body) == {"vendor_name": "Acme"}


def test_handles_nested_objects():
    """Brace counter must respect nesting, not bail at the first `}`."""
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    body = (
        'Here is the data:\n{"vendor_name": "Acme", '
        '"amount": {"value": 100, "confidence": 0.9}, "tax": null}'
    )
    out = _parse_ollama_json(body)
    assert out is not None
    assert out["vendor_name"] == "Acme"
    assert out["amount"]["confidence"] == 0.9


def test_ignores_braces_inside_strings():
    """A `}` inside a JSON string must NOT be counted as object close."""
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    body = '{"description": "Special offer { discount }", "amount": 100}'
    out = _parse_ollama_json(body)
    assert out == {"description": "Special offer { discount }", "amount": 100}


def test_handles_escaped_quotes_in_strings():
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    body = r'{"description": "He said \"hi\" then left", "amount": 100}'
    out = _parse_ollama_json(body)
    assert out is not None
    assert out["description"] == 'He said "hi" then left'


def test_returns_none_on_truncated_json():
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    # Cut off mid-object — no matching close brace
    body = '{"vendor_name": "Acme", "amount": 1'
    assert _parse_ollama_json(body) is None


@pytest.mark.parametrize(
    "wrapper",
    [
        '```json\n{"v": 1}\n```',
        '```json{"v": 1}```',
        '```\n{"v": 1}\n```',
        '{"v": 1}',
        'Output:\n```json\n{"v": 1}\n```\nDone.',
        'Sure! Here you go:\n{"v": 1}',
    ],
)
def test_recovers_v1_across_wrappers(wrapper):
    """A real-world matrix of how 11B models present JSON. All must yield {'v': 1}."""
    from app.services.extraction_adapters.ollama import _parse_ollama_json

    assert _parse_ollama_json(wrapper) == {"v": 1}
