"""Claude assistant adapter — Anthropic Messages API tool-use.

Selected only when an API key is configured. House style: a raw ``httpx`` POST
to ``https://api.anthropic.com/v1/messages`` (matches
``extraction_adapters/claude_vision.py``), not the SDK. The model id resolves
from config (``AP_ASSISTANT_MODEL`` → falls back to ``AP_EXTRACTION_MODEL``,
the claude-opus-4-8 family) — never hardcoded. Adaptive thinking per house
conventions for the Opus 4.x family.

The manual tool-use loop is capped at ``AP_ASSISTANT_MAX_TOOL_HOPS`` to bound
cost. Each ``tool_use`` block is executed via the orchestrator's tenant-bound,
audited ``run_tool`` closure — the adapter never touches the DB.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import settings
from app.services.assistant.base import AssistantAdapter, AssistantReply, RunTool, ToolInvocation
from app.services.assistant.dispatcher import register_assistant_adapter

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

_SYSTEM_PROMPT = (
    "You are the AP assistant for an accounts-payable application. You answer "
    "questions about the CURRENT tenant's accounts-payable data only. You may "
    "ONLY use the provided tools to read data — you cannot run arbitrary "
    "queries, you cannot access another tenant's data, and you must refuse any "
    "request that falls outside what the tools expose (e.g. modifying data, "
    "sending payments, or reading another organization's records). When a tool "
    "returns results, answer the user's question concisely and cite concrete "
    "figures from the tool output. Money values are exact — never round them "
    "away."
)


@register_assistant_adapter("claude")
class ClaudeAssistantAdapter(AssistantAdapter):
    """Anthropic Messages API tool-use adapter."""

    provider_name = "claude"

    def _model(self) -> str:
        return self.config.get("model") or settings.extraction_model

    def _headers(self) -> dict:
        return {
            "x-api-key": self.config.get("api_key", ""),
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    async def respond(
        self,
        *,
        message: str,
        history: list[dict],
        tool_specs: list[dict],
        run_tool: RunTool,
    ) -> AssistantReply:
        model = self._model()
        messages: list[dict] = list(history) + [{"role": "user", "content": message}]
        max_hops = max(1, settings.assistant_max_tool_hops)

        invocations: list[ToolInvocation] = []
        input_tokens = 0
        output_tokens = 0
        answer_parts: list[str] = []

        async with httpx.AsyncClient(timeout=60) as client:
            for _hop in range(max_hops):
                body = {
                    "model": model,
                    "max_tokens": 4096,
                    "system": _SYSTEM_PROMPT,
                    "thinking": {"type": "adaptive"},
                    "tools": tool_specs,
                    "messages": messages,
                }
                try:
                    resp = await client.post(_API_URL, json=body, headers=self._headers())
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Assistant claude API call failed: %s", exc.__class__.__name__)
                    answer_parts.append("Sorry — the assistant is unavailable right now.")
                    break

                if resp.status_code != 200:
                    logger.warning("Assistant claude API error %s", resp.status_code)
                    answer_parts.append("Sorry — the assistant is unavailable right now.")
                    break

                data = resp.json()
                usage = data.get("usage", {}) or {}
                input_tokens += int(usage.get("input_tokens", 0) or 0)
                output_tokens += int(usage.get("output_tokens", 0) or 0)

                content_blocks = data.get("content", []) or []
                # Collect any text the model emitted this hop.
                for block in content_blocks:
                    if block.get("type") == "text":
                        answer_parts.append(block.get("text", ""))

                stop_reason = data.get("stop_reason")
                if stop_reason != "tool_use":
                    break

                # Echo the assistant turn back, then run every tool_use block.
                messages.append({"role": "assistant", "content": content_blocks})
                tool_results = []
                for block in content_blocks:
                    if block.get("type") != "tool_use":
                        continue
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {}) or {}
                    invocation = await run_tool(tool_name, tool_input)
                    invocations.append(invocation)
                    if invocation.error:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.get("id"),
                                "is_error": True,
                                "content": invocation.error,
                            }
                        )
                    else:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.get("id"),
                                "content": json.dumps(invocation.result or {}),
                            }
                        )
                messages.append({"role": "user", "content": tool_results})

        answer = "\n".join(p for p in answer_parts if p).strip()
        if not answer:
            answer = "I wasn't able to produce an answer for that."
        return AssistantReply(
            answer=answer,
            tool_invocations=invocations,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=self.provider_name,
        )

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    _API_URL,
                    json={
                        "model": self._model(),
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                    headers=self._headers(),
                )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False
