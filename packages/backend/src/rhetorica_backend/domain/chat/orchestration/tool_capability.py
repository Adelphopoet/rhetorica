"""Tool capability contracts used by the orchestration tool executor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolCapability(Protocol):
    """Executable tool capability bound to a public tool name."""

    name: str
    requires_db_connectors: bool
    time_bound: bool

    def execute(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
        db_connectors: list[ConnectorItem],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BoundToolCapability:
    """Simple tool capability implementation backed by a callable."""

    name: str
    handler: Callable[..., dict[str, Any]]
    requires_db_connectors: bool = False
    time_bound: bool = False

    def execute(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
        db_connectors: list[ConnectorItem],
    ) -> dict[str, Any]:
        handler_kwargs: dict[str, Any] = {
            "request": request,
            "arguments": arguments,
            "state": state,
        }
        if self.requires_db_connectors:
            handler_kwargs["db_connectors"] = db_connectors
        return self.handler(**handler_kwargs)
