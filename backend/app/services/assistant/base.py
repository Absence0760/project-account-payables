"""Assistant adapter ABC + transport dataclasses.

Both adapters (``mock``, ``claude``) implement :class:`AssistantAdapter` and
call the orchestrator-supplied ``run_tool`` closure to execute a tool. Neither
adapter touches the DB or the audit infra directly — that keeps tenant
isolation and audit logging in one place (the orchestrator).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class ToolInvocation:
    """The structured record of one tool call within a turn."""

    tool: str
    args: dict  # PII-safe arg summary (same shape as the audit row)
    result: dict | None  # full ReturnModel.model_dump(mode="json") — chartable
    error: str | None = None


@dataclass
class AssistantReply:
    """An adapter's answer to one turn."""

    answer: str
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    input_tokens: int = 0  # mock = deterministic estimate; claude = real summed
    output_tokens: int = 0
    provider: str = ""


# ---------------------------------------------------------------------------
# Streaming protocol (adapter → orchestrator)
# ---------------------------------------------------------------------------
#
# ``respond_streaming`` yields these events as the answer is produced; the
# orchestrator forwards them onto the SSE wire. There are three kinds:
#
#   - ``TextDelta`` — a natural-language text span the model just emitted. On
#     the ``claude`` adapter this is a real Anthropic ``text_delta`` forwarded
#     token-by-token; on ``mock``/``ollama`` it is a coarse post-hoc chunk of
#     the assembled answer. Either way, concatenating every ``TextDelta.text``
#     reconstructs the final answer exactly (lossless).
#   - ``ToolDelta`` — a tool invocation just completed (the result of the
#     orchestrator's audited ``run_tool`` closure). Carries the same
#     ``ToolInvocation`` the non-streaming path produces.
#   - ``StreamDone`` — the terminal event, carrying the assembled
#     :class:`AssistantReply` (full tool_invocations + real summed usage). The
#     orchestrator reads usage/persistence off this — never off the individual
#     deltas — so token accounting under streaming equals the non-streaming
#     accounting.


@dataclass
class TextDelta:
    """One natural-language text span (a real model token span on claude)."""

    text: str


@dataclass
class ToolDelta:
    """A completed tool invocation, surfaced as it happens."""

    invocation: ToolInvocation


@dataclass
class StreamDone:
    """Terminal stream event — carries the assembled reply (tools + usage)."""

    reply: AssistantReply


StreamEvent = TextDelta | ToolDelta | StreamDone


# Orchestrator-owned executor: (tool_name, raw_args) -> ToolInvocation. It runs
# the tool against the tenant DB and writes the audit row; adapters only call it.
RunTool = Callable[[str, dict], Awaitable[ToolInvocation]]


class AssistantAdapter:
    """Base class for assistant adapters."""

    provider_name = "base"

    def __init__(self, config: dict):
        self.config = config

    async def respond(
        self,
        *,
        message: str,
        history: list[dict],
        tool_specs: list[dict],
        run_tool: RunTool,
    ) -> AssistantReply:
        raise NotImplementedError

    async def respond_streaming(
        self,
        *,
        message: str,
        history: list[dict],
        tool_specs: list[dict],
        run_tool: RunTool,
    ) -> AsyncIterator[StreamEvent]:
        """Yield :class:`StreamEvent`s as the answer is produced.

        Default implementation: run the non-streaming ``respond`` to completion,
        then replay it as ``ToolDelta``s followed by ``TextDelta`` chunks of the
        assembled answer (coarse, post-hoc), and finally ``StreamDone``. This
        keeps ``mock`` / ``ollama`` deterministic and network-free — they have
        nothing finer to stream. The ``claude`` adapter overrides this with true
        per-token Anthropic SSE passthrough.
        """
        from app.services.assistant.sse import iter_answer_chunks

        reply = await self.respond(
            message=message,
            history=history,
            tool_specs=tool_specs,
            run_tool=run_tool,
        )
        for inv in reply.tool_invocations:
            yield ToolDelta(invocation=inv)
        for chunk in iter_answer_chunks(reply.answer):
            yield TextDelta(text=chunk)
        yield StreamDone(reply=reply)

    async def test_connection(self) -> bool:
        raise NotImplementedError
