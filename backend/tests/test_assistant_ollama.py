"""Ollama assistant adapter — tool-use loop, token accounting, fail-soft.

Pure unit tests: ``httpx.AsyncClient`` is monkeypatched, so no Ollama server is
needed. The load-bearing behaviour is (1) the manual tool-use loop calls the
orchestrator's ``run_tool`` for each ``tool_call`` and assembles the final
answer, (2) ``prompt_eval_count`` / ``eval_count`` are summed into the usage
meter, and (3) the local-first rail — an unreachable Ollama (or a non-200)
fails soft to the deterministic ``mock`` router rather than erroring the turn.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.assistant.base import ToolInvocation
from app.services.assistant.ollama_adapter import OllamaAssistantAdapter, _to_ollama_tools
from app.services.assistant.tools import TOOL_SPECS


class _FakeResp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Async context-manager stand-in for ``httpx.AsyncClient``."""

    def __init__(self, *, post_responses=None, post_exc=None, get_response=None):
        self._post_responses = list(post_responses or [])
        self._post_exc = post_exc
        self._get_response = get_response
        self.post_bodies: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self.post_bodies.append(json)
        if self._post_exc is not None:
            raise self._post_exc
        return self._post_responses.pop(0)

    async def get(self, url):
        return self._get_response


def _patch_client(monkeypatch, client: _FakeClient):
    monkeypatch.setattr(
        "app.services.assistant.ollama_adapter.httpx.AsyncClient",
        lambda *a, **k: client,
    )


def _make_run_tool(record: list):
    async def run_tool(tool_name: str, raw_args: dict) -> ToolInvocation:
        record.append((tool_name, raw_args))
        return ToolInvocation(
            tool=tool_name,
            args=raw_args,
            result={"total": 1, "items": []},
            error=None,
        )

    return run_tool


def test_to_ollama_tools_shape():
    converted = _to_ollama_tools(TOOL_SPECS)
    assert len(converted) == len(TOOL_SPECS)
    first = converted[0]
    assert first["type"] == "function"
    assert first["function"]["name"] == TOOL_SPECS[0]["name"]
    # The Anthropic `input_schema` becomes the OpenAI-style `parameters`.
    assert first["function"]["parameters"] == TOOL_SPECS[0]["input_schema"]


@pytest.mark.asyncio
async def test_tool_use_loop_runs_tool_and_sums_tokens(monkeypatch):
    # Hop 1: the model asks to call a tool. Hop 2: it answers with prose.
    client = _FakeClient(
        post_responses=[
            _FakeResp(
                200,
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "list_pending_approvals",
                                    "arguments": {"assignee": "me"},
                                }
                            }
                        ],
                    },
                    "prompt_eval_count": 100,
                    "eval_count": 20,
                },
            ),
            _FakeResp(
                200,
                {
                    "message": {
                        "role": "assistant",
                        "content": "You have 1 invoice awaiting your approval.",
                    },
                    "prompt_eval_count": 30,
                    "eval_count": 12,
                },
            ),
        ]
    )
    _patch_client(monkeypatch, client)

    calls: list = []
    adapter = OllamaAssistantAdapter({"ollama_model": "qwen2.5-coder:7b"})
    reply = await adapter.respond(
        message="which approvals are waiting on me?",
        history=[],
        tool_specs=TOOL_SPECS,
        run_tool=_make_run_tool(calls),
    )

    assert calls == [("list_pending_approvals", {"assignee": "me"})]
    assert "awaiting your approval" in reply.answer
    assert reply.provider == "ollama"
    assert len(reply.tool_invocations) == 1
    # Tokens summed across BOTH hops.
    assert reply.input_tokens == 130
    assert reply.output_tokens == 32
    # The tools were converted and sent on the request.
    assert client.post_bodies[0]["tools"][0]["type"] == "function"


@pytest.mark.asyncio
async def test_string_arguments_are_json_decoded(monkeypatch):
    # Some models emit `arguments` as a JSON string rather than an object.
    client = _FakeClient(
        post_responses=[
            _FakeResp(
                200,
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "get_vendor_spend",
                                    "arguments": '{"period": "ytd", "top_n": 5}',
                                }
                            }
                        ]
                    },
                },
            ),
            _FakeResp(200, {"message": {"content": "Top vendors: …"}}),
        ]
    )
    _patch_client(monkeypatch, client)

    calls: list = []
    adapter = OllamaAssistantAdapter({})
    await adapter.respond(
        message="top vendors ytd",
        history=[],
        tool_specs=TOOL_SPECS,
        run_tool=_make_run_tool(calls),
    )
    assert calls == [("get_vendor_spend", {"period": "ytd", "top_n": 5})]


@pytest.mark.asyncio
async def test_connect_error_fails_soft_to_mock(monkeypatch):
    client = _FakeClient(post_exc=httpx.ConnectError("connection refused"))
    _patch_client(monkeypatch, client)

    calls: list = []
    adapter = OllamaAssistantAdapter({})
    reply = await adapter.respond(
        message="which vendors are we paying the most?",
        history=[],
        tool_specs=TOOL_SPECS,
        run_tool=_make_run_tool(calls),
    )
    # Fell back to the deterministic mock router: it still ran a tool + answered,
    # and the reply is attributed to `mock` (honest usage accounting).
    assert reply.provider == "mock"
    assert len(calls) == 1
    assert calls[0][0] == "get_vendor_spend"
    assert reply.answer


@pytest.mark.asyncio
async def test_non_200_fails_soft_to_mock(monkeypatch):
    client = _FakeClient(post_responses=[_FakeResp(500, {})])
    _patch_client(monkeypatch, client)

    calls: list = []
    adapter = OllamaAssistantAdapter({})
    reply = await adapter.respond(
        message="list rejected invoices",
        history=[],
        tool_specs=TOOL_SPECS,
        run_tool=_make_run_tool(calls),
    )
    assert reply.provider == "mock"
    assert calls and calls[0][0] == "list_invoices"


@pytest.mark.asyncio
async def test_empty_model_output_fails_soft_to_mock(monkeypatch):
    # Model returns neither text nor a tool call — don't ship an empty turn.
    client = _FakeClient(post_responses=[_FakeResp(200, {"message": {"content": ""}})])
    _patch_client(monkeypatch, client)

    calls: list = []
    adapter = OllamaAssistantAdapter({})
    reply = await adapter.respond(
        message="show my approval queue",
        history=[],
        tool_specs=TOOL_SPECS,
        run_tool=_make_run_tool(calls),
    )
    assert reply.provider == "mock"
    assert calls and calls[0][0] == "list_pending_approvals"


@pytest.mark.asyncio
async def test_degenerate_answer_with_embedded_tool_call_falls_back_to_mock(monkeypatch):
    # Hop 1: a real (text) tool call. Hop 2: the model apologizes and re-emits a
    # tool-call directive instead of formatting the result — a degenerate answer.
    client = _FakeClient(
        post_responses=[
            _FakeResp(
                200,
                {
                    "message": {
                        "content": (
                            '{"name": "list_pending_approvals", '
                            '"arguments": {"assignee": "me"}}'
                        )
                    }
                },
            ),
            _FakeResp(
                200,
                {
                    "message": {
                        "content": (
                            "I apologize. Let me try again with the correct call.\n\n"
                            '{"name": "list_pending_approvals", "arguments": {"assignee": "me"}}'
                        )
                    }
                },
            ),
        ]
    )
    _patch_client(monkeypatch, client)

    calls: list = []
    adapter = OllamaAssistantAdapter({})
    reply = await adapter.respond(
        message="which approvals have I been sitting on?",
        history=[],
        tool_specs=TOOL_SPECS,
        run_tool=_make_run_tool(calls),
    )
    # The raw-JSON/apology answer never reaches the user — deterministic templater wins.
    assert reply.provider == "mock"
    assert '"arguments"' not in reply.answer


@pytest.mark.asyncio
async def test_test_connection_checks_model_present(monkeypatch):
    client = _FakeClient(get_response=_FakeResp(200, {"models": [{"name": "qwen2.5-coder:7b"}]}))
    _patch_client(monkeypatch, client)
    adapter = OllamaAssistantAdapter({"ollama_model": "qwen2.5-coder:7b"})
    assert await adapter.test_connection() is True

    client_missing = _FakeClient(get_response=_FakeResp(200, {"models": [{"name": "llama3.1:8b"}]}))
    _patch_client(monkeypatch, client_missing)
    adapter_missing = OllamaAssistantAdapter({"ollama_model": "qwen2.5-coder:7b"})
    assert await adapter_missing.test_connection() is False
