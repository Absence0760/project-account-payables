"""Ollama local assistant adapter — tool-use over a local model.

Runs the conversational AP assistant against a **local, tool-capable** Ollama
text model (NOT the vision model used for extraction) via Ollama's
``/api/chat`` function-calling API. No key, no cloud — the model runs on the
contributor's laptop.

Local-first rail (#7): even though this is the committed default
(``AP_ASSISTANT_PROVIDER=ollama``), a fresh clone with no Ollama running — or
a model that isn't pulled / can't do tool-calling — **fails soft to the
deterministic ``mock`` adapter**, so ``pnpm dev`` still answers with zero
dependencies. The fallback is decided at call time (a sync dispatcher can't
probe the network), mirroring how the claude adapter downgrades on a missing
key.

The manual tool-use loop is capped at ``AP_ASSISTANT_MAX_TOOL_HOPS`` to bound
runaway cost, and each ``tool_call`` is executed via the orchestrator's
tenant-bound, audited ``run_tool`` closure — the adapter never touches the DB.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import settings
from app.services.assistant.base import AssistantAdapter, AssistantReply, RunTool, ToolInvocation
from app.services.assistant.dispatcher import register_assistant_adapter

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are the AP assistant for an accounts-payable application. You answer "
    "questions about the CURRENT tenant's accounts-payable data only. You may "
    "ONLY use the provided tools to read data — you cannot run arbitrary "
    "queries, you cannot access another tenant's data, and you must refuse any "
    "request that falls outside what the tools expose (e.g. modifying data, "
    "sending payments, or reading another organization's records). Prefer "
    "calling a tool over guessing. When a tool returns results, answer the "
    "user's question concisely and cite concrete figures from the tool output. "
    "Money values are exact — never round them away."
)


def _parse_text_tool_calls(content: str, valid_names: set[str]) -> list[dict]:
    """Recover tool calls a model emitted as JSON *text* instead of structured
    ``tool_calls``.

    Several Ollama models that don't fully implement the tool protocol still
    "want" to call a tool — they just print ``{"name": ..., "arguments": {...}}``
    (or a list of those) into ``message.content``. Rather than show the user raw
    JSON, recover the call(s). Returns Ollama-shaped ``tool_calls`` entries
    (``[{"function": {"name", "arguments"}}]``); empty when nothing parseable
    that names a known tool is found.
    """
    if not content:
        return []
    text = content.strip()
    if text.startswith("```"):
        # Strip a ```json fence if present.
        inner = text.split("```", 2)
        text = (inner[1] if len(inner) > 1 else text).lstrip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        text = text.split("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    candidates = parsed if isinstance(parsed, list) else [parsed]
    calls: list[dict] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        # Tolerate both the flat shape and the wrapped {"function": {...}} shape.
        fn = c.get("function") if isinstance(c.get("function"), dict) else c
        name = fn.get("name")
        if name not in valid_names:
            return []  # not a tool-call payload — leave content as prose
        args = fn.get("arguments", fn.get("parameters", {}))
        calls.append({"function": {"name": name, "arguments": args}})
    return calls


def _to_ollama_tools(tool_specs: list[dict]) -> list[dict]:
    """Convert the Anthropic-shaped specs (``{name, description, input_schema}``)
    to Ollama's OpenAI-style function schema (``{type, function:{...}}``)."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec.get("description", ""),
                "parameters": spec.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for spec in tool_specs
    ]


@register_assistant_adapter("ollama")
class OllamaAssistantAdapter(AssistantAdapter):
    """Local Ollama tool-use adapter — fails soft to ``mock`` when unavailable."""

    provider_name = "ollama"

    def _base_url(self) -> str:
        return self.config.get("ollama_base_url") or settings.ollama_base_url

    def _model(self) -> str:
        return self.config.get("ollama_model") or settings.assistant_ollama_model

    async def _fallback_to_mock(
        self, *, message: str, history: list[dict], tool_specs: list[dict], run_tool: RunTool
    ) -> AssistantReply:
        """Delegate to the deterministic mock adapter (local-first rail)."""
        # Lazy import: both adapters are imported by the dispatcher at module
        # load, so a top-level import here would cycle (mock → dispatcher →
        # ollama → mock) during initialization.
        from app.services.assistant.mock_adapter import MockAssistantAdapter

        return await MockAssistantAdapter(self.config).respond(
            message=message, history=history, tool_specs=tool_specs, run_tool=run_tool
        )

    async def respond(
        self,
        *,
        message: str,
        history: list[dict],
        tool_specs: list[dict],
        run_tool: RunTool,
    ) -> AssistantReply:
        model = self._model()
        ollama_tools = _to_ollama_tools(tool_specs)
        valid_names = {spec["name"] for spec in tool_specs}
        max_hops = max(1, settings.assistant_max_tool_hops)

        messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})

        invocations: list[ToolInvocation] = []
        input_tokens = 0
        output_tokens = 0
        answer_parts: list[str] = []

        try:
            # Ollama serialises inference per-model; a single assistant turn with
            # a few short tool hops fits comfortably under 120s on a 7B model.
            async with httpx.AsyncClient(timeout=120) as client:
                for _hop in range(max_hops):
                    body = {
                        "model": model,
                        "messages": messages,
                        "tools": ollama_tools,
                        "stream": False,
                        "options": {"num_predict": 2048},
                    }
                    resp = await client.post(f"{self._base_url()}/api/chat", json=body)
                    if resp.status_code != 200:
                        logger.warning(
                            "Ollama assistant error %s — falling back to mock", resp.status_code
                        )
                        return await self._fallback_to_mock(
                            message=message,
                            history=history,
                            tool_specs=tool_specs,
                            run_tool=run_tool,
                        )

                    data = resp.json()
                    input_tokens += int(data.get("prompt_eval_count", 0) or 0)
                    output_tokens += int(data.get("eval_count", 0) or 0)

                    msg = data.get("message", {}) or {}
                    text = (msg.get("content") or "").strip()
                    tool_calls = msg.get("tool_calls") or []

                    # Some models emit the tool call as JSON *text* instead of a
                    # structured `tool_calls` field — recover it so we execute
                    # the tool rather than show the user raw JSON.
                    if not tool_calls and text:
                        recovered = _parse_text_tool_calls(text, valid_names)
                        if recovered:
                            tool_calls = recovered
                            text = ""  # it was a tool-call payload, not prose

                    if text:
                        answer_parts.append(text)
                    if not tool_calls:
                        break

                    # Echo the assistant turn (with its tool_calls) back, then run
                    # each call via the audited orchestrator closure.
                    messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
                    for call in tool_calls:
                        fn = call.get("function", {}) or {}
                        tool_name = fn.get("name", "")
                        raw_args = fn.get("arguments", {})
                        if isinstance(raw_args, str):
                            try:
                                raw_args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                raw_args = {}
                        invocation = await run_tool(tool_name, raw_args or {})
                        invocations.append(invocation)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_name": tool_name,
                                "content": (
                                    invocation.error
                                    if invocation.error
                                    else json.dumps(invocation.result or {})
                                ),
                            }
                        )
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            logger.warning(
                "Ollama assistant unavailable (%s) — falling back to mock",
                exc.__class__.__name__,
            )
            return await self._fallback_to_mock(
                message=message, history=history, tool_specs=tool_specs, run_tool=run_tool
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Ollama assistant call failed (%s) — falling back to mock",
                exc.__class__.__name__,
            )
            return await self._fallback_to_mock(
                message=message, history=history, tool_specs=tool_specs, run_tool=run_tool
            )

        answer = "\n".join(p for p in answer_parts if p).strip()
        if not answer:
            # The model produced no prose (e.g. it only emitted tool-call JSON,
            # or nothing at all). Rather than ship raw JSON or a stub, hand off
            # to the deterministic templater for a clean answer — it re-runs the
            # same read-only tool and formats it.
            return await self._fallback_to_mock(
                message=message, history=history, tool_specs=tool_specs, run_tool=run_tool
            )
        return AssistantReply(
            answer=answer,
            tool_invocations=invocations,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=self.provider_name,
        )

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url()}/api/tags")
            if resp.status_code != 200:
                return False
            models = [m["name"] for m in resp.json().get("models", [])]
            target = self._model()
            return any(target in m for m in models)
        except Exception:  # noqa: BLE001
            return False
