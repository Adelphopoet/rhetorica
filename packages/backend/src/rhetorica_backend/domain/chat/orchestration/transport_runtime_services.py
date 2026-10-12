"""Transport-specific runtime services for sync and streaming shells."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any, cast

from rhetorica_backend.contracts.chat import (
    ChatAskRequest,
    ChunkEvent,
    ErrorEvent,
    StreamEvent,
    ThinkingEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.agent_runtime import (
    AgentIterationContext,
    AgentRuntimeControl,
    AgentRuntimeInput,
    AgentRuntimePreparedContent,
    AgentRuntimeRunItemEvent,
    AgentRuntimeTransport,
)
from rhetorica_backend.domain.chat.orchestration.llm_failure import (
    LlmFailureMessageFormatter,
)
from rhetorica_backend.domain.chat.orchestration.models import PreparedRuntimeTurnInput

THINKING_TAG_PATTERN = re.compile(r"<(think|thinking)>(.*?)</\1>", flags=re.IGNORECASE | re.DOTALL)
JSON_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", flags=re.IGNORECASE | re.DOTALL
)


class SyncToolLoopRuntimeService:
    """Runs the sync tool loop behind a transport-specific adapter."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        execution_core: Any,
    ) -> None:
        self._logger = logger
        self._execution_core = execution_core

    async def run(
        self,
        *,
        llm_client: Any,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        state: Any,
        tool_executor: Any,
        agent_session: Any | None = None,
    ) -> dict[str, Any]:
        def before_iteration(
            context: AgentIterationContext[Any],
        ) -> AgentRuntimeControl[dict[str, Any]] | None:
            self._logger.info(
                "LLM tool orchestration iteration %d/%d, warnings=%d, context_tables=%d, prompt_version=%s",
                context.iteration + 1,
                context.max_iterations,
                len(state.warnings),
                len(state.context_tables),
                state.prompt_version,
            )
            return None

        def prepare_response_content(
            raw_content: str,
            reasoning_content: str,
        ) -> AgentRuntimePreparedContent:
            StreamingResponseContentService.emit_thinking_diagnostics(
                raw_content,
                reasoning_content=reasoning_content,
            )
            return AgentRuntimePreparedContent(
                raw_content=raw_content,
                completion_raw_content=raw_content,
                finalizable_content=raw_content.strip(),
            )

        def on_completion(
            completion: Any,
            _prepared_content: AgentRuntimePreparedContent | None,
        ) -> None:
            if completion.completion_type == "success":
                self._logger.info(
                    "LLM tool orchestration completed successfully, generated_sql=%s, "
                    "prompt_version=%s, tool_calls=%d, preflight_pass=%d, "
                    "preflight_fail=%d, preflight_uncertain=%d",
                    "yes" if state.generated_sql else "no",
                    state.prompt_version,
                    state.tool_call_count,
                    state.preflight_pass,
                    state.preflight_fail,
                    state.preflight_uncertain,
                )
            elif completion.completion_type == "loop_limit":
                self._logger.warning(
                    "LLM tool orchestration failed to complete within limit, partial_result=%s, "
                    "prompt_version=%s, tool_calls=%d, preflight_pass=%d, "
                    "preflight_fail=%d, preflight_uncertain=%d",
                    "yes" if (state.generated_sql or state.execution) else "no",
                    state.prompt_version,
                    state.tool_call_count,
                    state.preflight_pass,
                    state.preflight_fail,
                    state.preflight_uncertain,
                )

        return cast(
            dict[str, Any],
            await self._execution_core.run_loop(
                runtime=AgentRuntimeInput(
                    llm_client=llm_client,
                    request=request,
                    db_connectors=db_connectors,
                    messages=messages,
                    tools=tools,
                    state=state,
                    tool_executor=tool_executor,
                    agent_session=agent_session,
                ),
                transport=AgentRuntimeTransport(
                    before_iteration=before_iteration,
                    prepare_response_content=prepare_response_content,
                    on_llm_failure=lambda _exc, _state: None,
                    on_completion=on_completion,
                    adapt_payload=lambda payload: payload,
                ),
            ),
        )


class StreamingResponseContentService:
    """Owns streaming-specific content parsing and event shaping."""

    @classmethod
    def emit_thinking_diagnostics(
        cls,
        content: str,
        *,
        reasoning_content: str = "",
        emit: Callable[..., None] = shared_kernel.emit_diagnostic_event,
    ) -> None:
        thinking_blocks = cls.thinking_blocks(
            content,
            reasoning_content=reasoning_content,
        )
        for index, block in enumerate(thinking_blocks, start=1):
            emit(
                "llm.turn.thinking_block",
                block_index=index,
                content=block,
            )

    def process_content_for_thinking(
        self,
        content: str,
        *,
        reasoning_content: str = "",
    ) -> list[ChunkEvent | ThinkingEvent]:
        events: list[ChunkEvent | ThinkingEvent] = []
        thinking_blocks = self.thinking_blocks(
            content,
            reasoning_content=reasoning_content,
        )
        _, visible_content = self.extract_thinking_blocks(content)
        for block in thinking_blocks:
            events.append(ThinkingEvent(content=block))
        if visible_content.strip():
            events.append(ChunkEvent(content=visible_content.strip()))
        return events

    def thinking_events_from_content(
        self,
        content: str,
        *,
        reasoning_content: str = "",
    ) -> list[ThinkingEvent]:
        thinking_blocks = self.thinking_blocks(
            content,
            reasoning_content=reasoning_content,
        )
        return [ThinkingEvent(content=block) for block in thinking_blocks]

    @classmethod
    def thinking_blocks(
        cls,
        content: str,
        *,
        reasoning_content: str = "",
    ) -> list[str]:
        tagged_blocks, _ = cls.extract_thinking_blocks(content)
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            return [reasoning_content, *tagged_blocks]
        return tagged_blocks

    def visible_chunk_event(self, content: str) -> ChunkEvent | None:
        _, visible_content = self.extract_thinking_blocks(content)
        if not visible_content.strip():
            return None
        _, parsed_ok = self.parse_json_payload(content)
        if parsed_ok:
            return None
        return ChunkEvent(content=visible_content.strip())

    def process_final_content(self, content: str) -> list[ChunkEvent | ThinkingEvent]:
        events = self.process_content_for_thinking(content)
        _, parsed_ok = self.parse_json_payload(content)
        if parsed_ok:
            return [event for event in events if isinstance(event, ThinkingEvent)]
        return events

    @staticmethod
    def parse_json_payload(content: str) -> tuple[dict[str, Any], bool]:
        _, visible_content = StreamingResponseContentService.extract_thinking_blocks(content)
        cleaned_content = StreamingResponseContentService.strip_json_markdown_fence(
            visible_content
        ).strip()
        if not cleaned_content:
            return {}, False
        try:
            parsed = json.loads(cleaned_content)
        except json.JSONDecodeError:
            start = cleaned_content.find("{")
            end = cleaned_content.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    parsed = json.loads(cleaned_content[start:end])
                except json.JSONDecodeError:
                    return {}, False
                else:
                    if isinstance(parsed, dict):
                        return parsed, True
                    return {}, False
            return {}, False
        else:
            if isinstance(parsed, dict):
                return parsed, True
            return {}, False

    @staticmethod
    def extract_thinking_blocks(content: str) -> tuple[list[str], str]:
        thinking_blocks: list[str] = []

        def _replace(match: re.Match[str]) -> str:
            inner = match.group(2).strip()
            if inner:
                thinking_blocks.append(inner)
            return " "

        visible_content = THINKING_TAG_PATTERN.sub(_replace, content)
        return thinking_blocks, visible_content

    @staticmethod
    def strip_json_markdown_fence(content: str) -> str:
        match = JSON_FENCE_PATTERN.match(content)
        if match:
            return match.group(1).strip()
        return content

    @staticmethod
    def summarize_tool_result(result: dict[str, Any]) -> str:
        if result.get("ok"):
            if "count" in result:
                return f"Found {result['count']} items"
            if "tables" in result:
                return f"Retrieved {len(result['tables'])} tables"
            return "Success"
        error = (
            result.get("error_message")
            or result.get("error")
            or result.get("error_code")
            or "Unknown error"
        )
        return f"Failed: {error}"


class StreamingRuntimeLoopService:
    """Runs prepared streaming runtime flow and emits transport events."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        execution_core: Any,
        content_service: StreamingResponseContentService,
        tool_loop_result_factory: Callable[..., Any],
    ) -> None:
        self._logger = logger
        self._execution_core = execution_core
        self._content_service = content_service
        self._tool_loop_result_factory = tool_loop_result_factory

    async def run_prepared(
        self,
        *,
        runtime: PreparedRuntimeTurnInput[Any],
        cancel_event: asyncio.Event | None,
        on_event: Callable[[StreamEvent], None] | None,
    ) -> Any:
        events: list[StreamEvent] = []
        state = runtime.state
        streamed_text = False
        streamed_reasoning = False
        def record_event(event: StreamEvent) -> None:
            if on_event is None:
                events.append(event)
            if on_event is not None:
                on_event(event)

        if cancel_event and cancel_event.is_set():
            self._mark_runtime_cancelled(state)
            return self._tool_loop_result_factory(
                payload={
                    "answer": "Request was cancelled",
                    "sql": None,
                    "target": None,
                    "selected_database_connector": None,
                    "metadata_sources": [],
                    "context_tables": [],
                    "prompt_version": state.prompt_version,
                    "warnings": ["cancelled"],
                    "execution": None,
                    "visualization": None,
                },
                events=events,
            )

        def cancelled_payload() -> dict[str, Any]:
            self._mark_runtime_cancelled(state)
            return {
                "answer": "Request was cancelled",
                "sql": None,
                "target": None,
                "selected_database_connector": None,
                "metadata_sources": [],
                "context_tables": [],
                "prompt_version": state.prompt_version,
                "warnings": ["cancelled"],
                "execution": None,
                "visualization": None,
                "capability_trace": shared_kernel.build_capability_trace_from_state(
                    state=state,
                    metadata_sources=sorted(state.metadata_sources),
                    warnings=["cancelled"],
                    execution=state.execution,
                ).model_dump(mode="json"),
            }

        def before_iteration(
            context: AgentIterationContext[Any],
        ) -> AgentRuntimeControl[Any] | None:
            if cancel_event and cancel_event.is_set():
                self._logger.info("Tool loop cancelled at iteration %d", context.iteration)
                return AgentRuntimeControl(
                    done=True,
                    payload=self._tool_loop_result_factory(
                        payload=cancelled_payload(),
                        events=events,
                    ),
                )
            self._logger.info(
                "LLM tool orchestration iteration %d/%d",
                context.iteration + 1,
                context.max_iterations,
            )
            return None

        def on_llm_failure(exc: Exception, _state: Any) -> None:
            connector_name = runtime.request.llm_connector_name
            record_event(
                ErrorEvent(
                    code="llm_request_failed",
                    message=LlmFailureMessageFormatter.format(
                        connector_name=connector_name,
                        error=exc,
                    ),
                    details={
                        "type": type(exc).__name__,
                        "llm_connector_name": connector_name,
                    },
                )
            )

        def prepare_response_content(
            raw_content: str,
            reasoning_content: str,
        ) -> AgentRuntimePreparedContent:
            self._content_service.emit_thinking_diagnostics(
                raw_content,
                reasoning_content=reasoning_content,
            )
            if not streamed_reasoning:
                for event in self._content_service.thinking_events_from_content(
                    raw_content,
                    reasoning_content=reasoning_content,
                ):
                    record_event(event)
            _, stripped_visible_content = self._content_service.extract_thinking_blocks(raw_content)
            return AgentRuntimePreparedContent(
                raw_content=raw_content,
                completion_raw_content=raw_content,
                finalizable_content=self._content_service.strip_json_markdown_fence(
                    stripped_visible_content
                ).strip(),
            )

        def on_completion(
            completion: Any,
            prepared_content: AgentRuntimePreparedContent | None,
        ) -> None:
            if completion.completion_type != "success" or prepared_content is None:
                return
            if not streamed_text:
                visible_chunk = self._content_service.visible_chunk_event(
                    prepared_content.completion_raw_content
                )
                if visible_chunk is not None:
                    record_event(visible_chunk)
            self._logger.info("LLM tool orchestration completed successfully")

        def on_text_delta(content: str) -> None:
            nonlocal streamed_text
            if not content:
                return
            streamed_text = True
            record_event(ChunkEvent(content=content))

        def on_reasoning_delta(content: str) -> None:
            nonlocal streamed_reasoning
            if not content:
                return
            streamed_reasoning = True
            record_event(ThinkingEvent(content=content, delta=True))

        def on_run_item(event: AgentRuntimeRunItemEvent) -> None:
            if event.kind == "tool_called":
                record_event(
                    ToolCallStartEvent(
                        tool_name=event.tool_name or "unknown_tool",
                        tool_call_id=event.tool_call_id,
                        arguments=event.arguments,
                    )
                )
                return
            tool_result = event.tool_result or {}
            success = tool_result.get("ok", tool_result.get("status") == "succeeded")
            record_event(
                ToolCallEndEvent(
                    tool_name=event.tool_name or "unknown_tool",
                    tool_call_id=event.tool_call_id,
                    result_summary=self._content_service.summarize_tool_result(tool_result),
                    success=bool(success),
                )
            )
            if (
                tool_result.get("error_code") == "tool_timeout"
                and tool_result.get("can_continue") is False
            ):
                record_event(
                    ErrorEvent(
                        code="tool_timeout",
                        message=str(tool_result.get("user_message") or "Tool execution timed out."),
                        details={
                            "tool_name": event.tool_name,
                            "tool_call_id": event.tool_call_id,
                            "timeout_seconds": tool_result.get("timeout_seconds"),
                        },
                    )
                )

        def on_cancelled() -> Any:
            return self._tool_loop_result_factory(
                payload=cancelled_payload(),
                events=events,
            )

        return cast(
            Any,
            await self._execution_core.run_loop(
                runtime=AgentRuntimeInput(
                    llm_client=runtime.llm_client,
                    request=runtime.request,
                    db_connectors=runtime.db_connectors,
                    messages=runtime.messages,
                    tools=runtime.tools,
                    state=runtime.state,
                    tool_executor=runtime.tool_executor,
                    agent_session=getattr(runtime, "agent_session", None),
                    streamed=True,
                    cancel_event=cancel_event,
                ),
                transport=AgentRuntimeTransport(
                    before_iteration=before_iteration,
                    prepare_response_content=prepare_response_content,
                    on_llm_failure=on_llm_failure,
                    on_completion=on_completion,
                    adapt_payload=lambda payload: self._tool_loop_result_factory(
                        payload=payload,
                        events=events,
                    ),
                    on_text_delta=on_text_delta,
                    on_reasoning_delta=on_reasoning_delta,
                    on_run_item=on_run_item,
                    on_cancelled=on_cancelled,
                ),
            ),
        )

    @staticmethod
    def _mark_runtime_cancelled(state: Any) -> None:
        if "cancelled" not in getattr(state, "warnings", []):
            state.add_warning("cancelled")
