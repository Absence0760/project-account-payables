"""Assistant adapter ABC + transport dataclasses.

Both adapters (``mock``, ``claude``) implement :class:`AssistantAdapter` and
call the orchestrator-supplied ``run_tool`` closure to execute a tool. Neither
adapter touches the DB or the audit infra directly — that keeps tenant
isolation and audit logging in one place (the orchestrator).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
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

    async def test_connection(self) -> bool:
        raise NotImplementedError
