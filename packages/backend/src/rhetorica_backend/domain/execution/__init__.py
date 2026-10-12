"""Public execution domain API without eager composition-root imports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rhetorica_backend.domain.execution.service import ExecutionService


def build_execution_service(*args: Any, **kwargs: Any) -> Any:
    """Load infrastructure composition only when an application asks for it."""
    from rhetorica_backend.domain.execution.bootstrap import (
        build_execution_service as build,
    )

    return build(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if name == "ExecutionService":
        from rhetorica_backend.domain.execution.service import ExecutionService

        return ExecutionService
    raise AttributeError(name)


__all__ = ["ExecutionService", "build_execution_service"]
