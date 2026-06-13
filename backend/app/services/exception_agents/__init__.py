"""Exception-agent package — autonomous resolution of flagged invoices."""

# Import resolver modules for their @register_exception_agent side effects.
from app.services.exception_agents import resolvers  # noqa: E402,F401
from app.services.exception_agents.base import (
    ACTION_AUTO_RESOLVED,
    ACTION_ESCALATED,
    ACTION_NO_ACTION,
    AgentEvaluation,
    ExceptionResolver,
)
from app.services.exception_agents.coordinator import ExceptionNotActionable, run_agent
from app.services.exception_agents.registry import (
    get_resolver,
    register_exception_agent,
    registered_exception_types,
)

__all__ = [
    "ACTION_AUTO_RESOLVED",
    "ACTION_ESCALATED",
    "ACTION_NO_ACTION",
    "AgentEvaluation",
    "ExceptionResolver",
    "ExceptionNotActionable",
    "run_agent",
    "get_resolver",
    "register_exception_agent",
    "registered_exception_types",
]
