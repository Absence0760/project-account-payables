from __future__ import annotations

from app.services.exception_agents.base import ExceptionResolver

_RESOLVER_REGISTRY: dict[str, type[ExceptionResolver]] = {}


def register_exception_agent(exception_type: str):
    """Register a resolver class against the exception_type it handles."""

    def wrapper(cls: type[ExceptionResolver]):
        _RESOLVER_REGISTRY[exception_type] = cls
        return cls

    return wrapper


def get_resolver(exception_type: str) -> ExceptionResolver | None:
    cls = _RESOLVER_REGISTRY.get(exception_type)
    return cls() if cls else None


def registered_exception_types() -> list[str]:
    return sorted(_RESOLVER_REGISTRY.keys())
