"""Shared tool-loop engine for sync and streaming chat orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    final_response_json_schema,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.settings import get_max_iterations

TResult = TypeVar("TResult")


@dataclass(slots=True)
class TurnEngineControl(Generic[TResult]):
    """Mutable control object returned by turn-engine callbacks."""

    done: bool = False
    payload: TResult | None = None
    tools: list[dict[str, Any]] | None = None
    response_schema: dict[str, Any] | None = None


@dataclass(slots=True)
class TurnIterationContext:
    """State handed to per-iteration hooks."""

    iteration: int
    max_iterations: int
    request: ChatAskRequest
    db_connectors: list[ConnectorItem]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    response_schema: dict[str, Any] | None
    state: ToolOrchestrationState


@dataclass(slots=True)
class TurnResponseContext:
    """LLM response context for sync/streaming-specific handlers."""

    iteration: int
    max_iterations: int
    request: ChatAskRequest
    db_connectors: list[ConnectorItem]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    response_schema: dict[str, Any] | None
    state: ToolOrchestrationState
    response: Any


class TurnEngine(Generic[TResult]):
    """Reusable outer loop for tool-driven LLM turns."""

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        state: ToolOrchestrationState,
        ensure_dynamic_tools: Any,
        before_iteration: Any,
        on_response: Any,
        on_llm_failure: Any,
        on_limit_reached: Any,
        on_tools_disabled: Any,
        on_structured_disabled: Any,
    ) -> None:
        self._llm_client = llm_client
        self._request = request
        self._db_connectors = db_connectors
        self._messages = messages
        self._tools = tools
        self._state = state
        self._ensure_dynamic_tools = ensure_dynamic_tools
        self._before_iteration = before_iteration
        self._on_response = on_response
        self._on_llm_failure = on_llm_failure
        self._on_limit_reached = on_limit_reached
        self._on_tools_disabled = on_tools_disabled
        self._on_structured_disabled = on_structured_disabled

    def run(self) -> TResult:
        max_iterations = get_max_iterations()
        response_schema = None if self._tools else final_response_json_schema()
        tools = list(self._tools)
        prompt_budget_service = (
            self._llm_client.build_prompt_budget_service()
            if hasattr(self._llm_client, "build_prompt_budget_service")
            else None
        )

        for iteration in range(max_iterations):
            tools = self._ensure_dynamic_tools(
                tools=tools,
                request=self._request,
                db_connectors=self._db_connectors,
                state=self._state,
            )
            iteration_context = TurnIterationContext(
                iteration=iteration,
                max_iterations=max_iterations,
                request=self._request,
                db_connectors=self._db_connectors,
                messages=self._messages,
                tools=tools,
                response_schema=response_schema,
                state=self._state,
            )
            pre_control = self._before_iteration(iteration_context)
            if pre_control is not None:
                if pre_control.tools is not None:
                    tools = pre_control.tools
                if pre_control.response_schema is not None:
                    response_schema = pre_control.response_schema
                if pre_control.done:
                    return pre_control.payload

            if prompt_budget_service is not None:
                budget_result = prompt_budget_service.fit_request_messages(
                    messages=self._messages,
                    tools=tools,
                    response_schema=response_schema,
                )
                self._messages[:] = budget_result.messages
                if budget_result.tools is not None:
                    tools = budget_result.tools
                for warning in budget_result.warnings:
                    self._state.add_warning(warning)

            try:
                response, tools_disabled, structured_disabled = (
                    self._llm_client.chat_completion_with_fallback(
                        messages=self._messages,
                        tools=tools,
                        response_schema=response_schema,
                        tool_choice="auto",
                    )
                )
                # Collect LLM usage from this call and store in state
                for usage in self._llm_client.collect_usages():
                    self._state.llm_usages.append(usage)
            except Exception as exc:
                return self._on_llm_failure(exc, iteration_context)

            if tools_disabled:
                tools, response_schema = self._on_tools_disabled(
                    iteration_context,
                    tools=tools,
                    response_schema=response_schema,
                )
            if structured_disabled:
                tools, response_schema = self._on_structured_disabled(
                    iteration_context,
                    tools=tools,
                    response_schema=response_schema,
                )

            response_context = TurnResponseContext(
                iteration=iteration,
                max_iterations=max_iterations,
                request=self._request,
                db_connectors=self._db_connectors,
                messages=self._messages,
                tools=tools,
                response_schema=response_schema,
                state=self._state,
                response=response,
            )
            control = self._on_response(response_context)
            if control.tools is not None:
                tools = control.tools
            if control.response_schema is not None:
                response_schema = control.response_schema
            if control.done:
                return control.payload

        return self._on_limit_reached(
            TurnIterationContext(
                iteration=max_iterations,
                max_iterations=max_iterations,
                request=self._request,
                db_connectors=self._db_connectors,
                messages=self._messages,
                tools=tools,
                response_schema=response_schema,
                state=self._state,
            )
        )
