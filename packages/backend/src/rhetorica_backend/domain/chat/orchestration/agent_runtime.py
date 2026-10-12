"""Typed application boundary for agent turn runtimes and transports."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, Protocol, TypeVar

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.models import ToolExecutorPort, TurnCompletion
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState

TState = TypeVar("TState", bound=ToolOrchestrationState)
TResult = TypeVar("TResult")


@dataclass(slots=True)
class AgentRuntimeRunItemEvent:
    """SDK run-item event translated without leaking SDK types across the port."""

    kind: Literal["tool_called", "tool_output"]
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None


@dataclass(slots=True)
class AgentRuntimePreparedContent:
    """Transport-specific content normalization for an assistant output."""

    raw_content: str
    completion_raw_content: str
    finalizable_content: str


@dataclass(slots=True)
class AgentRuntimeInput(Generic[TState]):
    """Complete application-owned input for one SDK agent turn."""

    llm_client: LlmClient
    request: ChatAskRequest
    db_connectors: list[ConnectorItem]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    state: TState
    tool_executor: ToolExecutorPort
    agent_session: Any | None = None
    streamed: bool = False
    cancel_event: asyncio.Event | None = None


@dataclass(slots=True)
class AgentIterationContext(Generic[TState]):
    """Exact lifecycle snapshot emitted before one SDK model call."""

    iteration: int
    max_iterations: int
    request: ChatAskRequest
    db_connectors: list[ConnectorItem]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    response_schema: dict[str, Any] | None
    state: TState


@dataclass(slots=True)
class AgentRuntimeControl(Generic[TResult]):
    """Optional transport stop signal checked before an SDK model call."""

    done: bool = False
    payload: TResult | None = None


@dataclass(slots=True)
class AgentRuntimeTransport(Generic[TState, TResult]):
    """Transport-specific callbacks around the shared SDK runtime."""

    before_iteration: Callable[[AgentIterationContext[TState]], AgentRuntimeControl[TResult] | None]
    prepare_response_content: Callable[[str, str], AgentRuntimePreparedContent]
    on_llm_failure: Callable[[Exception, TState], None]
    on_completion: Callable[[TurnCompletion, AgentRuntimePreparedContent | None], None]
    adapt_payload: Callable[[dict[str, Any]], TResult]
    on_text_delta: Callable[[str], None] | None = None
    on_reasoning_delta: Callable[[str], None] | None = None
    on_run_item: Callable[[AgentRuntimeRunItemEvent], None] | None = None
    on_cancelled: Callable[[], TResult] | None = None


class AgentTurnRuntimePort(Protocol):
    """Internal boundary hiding the concrete agent runtime implementation."""

    async def run(
        self,
        *,
        runtime: AgentRuntimeInput[Any],
        transport: AgentRuntimeTransport[Any, Any],
    ) -> Any: ...
