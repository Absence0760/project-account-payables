"""Optional LLM polish for an exception-agent's decision rationale.

Mirrors ``services.audit_summary``'s fail-soft contract: a deterministic
``template`` is ALWAYS the fallback, an LLM is consulted only when a key is
configured, and there is zero network call in the no-key/local-dev default. The
``http_post`` argument is an injection point for tests.

Invariant: the *decision* (action + confidence + the actual amount change) is
100% rules-derived in the resolver. The LLM only rewords the rationale string —
the agent behaves identically (and offline) with no key.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _resolve_config(org_settings: dict | None) -> dict:
    """Reuse the extraction key/model (same pattern as audit_summary).
    Empty api_key → template path (the local-dev default)."""
    extraction = (org_settings or {}).get("extraction", {})
    if extraction.get("program_type") == "byok":
        return {
            "api_key": extraction.get("api_key", ""),
            "model": extraction.get("model") or settings.extraction_model,
        }
    return {"api_key": settings.anthropic_api_key, "model": settings.extraction_model}


def _build_prompt(template: str, facts: dict) -> str:
    return (
        "You are writing a one-sentence plain-English rationale for an "
        "accounts-payable exception that an autonomous agent just resolved. "
        "Keep it factual, concise, and free of any banking / PII details.\n\n"
        f"Deterministic draft (you may polish, but keep its meaning):\n{template}\n\n"
        f"Facts (JSON):\n{json.dumps(facts, indent=2)}\n\n"
        "Respond with a single JSON object, no surrounding prose:\n\n"
        '{"rationale": "<one concise sentence>"}'
    )


def _parse_response(text: str) -> str | None:
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    rationale = data.get("rationale")
    if isinstance(rationale, str) and rationale.strip():
        return rationale.strip()
    return None


async def build_rationale(
    org_settings: dict | None,
    *,
    template: str,
    facts: dict,
    http_post=None,
) -> str:
    """Return a human-readable rationale. Deterministic ``template`` is always
    the fallback; an LLM is consulted ONLY when a key is configured. Any failure
    (no key, network, parse) returns ``template``. No network call in the
    no-key/local default."""
    cfg = _resolve_config(org_settings)
    api_key = cfg.get("api_key") or ""
    if not api_key:
        return template  # local-first default: deterministic, no network.

    model = cfg.get("model") or settings.extraction_model
    body = {
        "model": model,
        "max_tokens": 300,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": _build_prompt(template, facts)}]}
        ],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        if http_post is not None:
            resp = await http_post(json=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    json=body,
                    headers=headers,
                )
    except Exception:
        logger.exception("Agent rationale LLM call failed; using template")
        return template

    if resp.status_code != 200:
        logger.warning("Agent rationale: LLM returned %s; using template", resp.status_code)
        return template

    data = resp.json()
    blocks = data.get("content") or []
    text = next((b.get("text", "") for b in blocks if b.get("type") == "text"), "")
    parsed = _parse_response(text) if text else None
    return parsed if parsed else template
