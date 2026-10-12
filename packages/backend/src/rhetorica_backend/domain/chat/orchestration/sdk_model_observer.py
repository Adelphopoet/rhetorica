"""Observe public Agents SDK hooks/events without changing model execution."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from openai.types.responses import (
    Response,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseReasoningItem,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningTextDeltaEvent,
    ResponseTextDeltaEvent,
)
from rhetorica_backend.domain.chat.llm.llm_client import LlmCallDiagnostics, LlmCallUsage
from rhetorica_backend.domain.chat.orchestration.agent_runtime import AgentRuntimeInput

from agents import ModelTimeoutError, RunHooks
from agents.items import ModelResponse


@dataclass
class _Call:
    usage_index: int
    started: float
    diagnostics: LlmCallDiagnostics
    logged: bool = False


class SdkModelCallObserver:
    """One observer per run; stream FIFO handles producer hooks ahead of the consumer."""

    def __init__(self, runtime: AgentRuntimeInput[Any], logger: logging.Logger) -> None:
        self._runtime = runtime
        self._logger = logger
        self._run_id = str(uuid4())
        self._calls: list[_Call] = []
        self._streams: deque[_Call] = deque()

    def start(self, *, phase: str, streamed: bool) -> _Call:
        diagnostic = LlmCallDiagnostics(
            run_id=self._run_id,
            call_id=str(uuid4()),
            call_index=len(self._calls) + 1,
            streamed=streamed,
            started_at=datetime.now(UTC).isoformat(),
        )
        call = _Call(len(self._runtime.state.llm_usages), perf_counter(), diagnostic)
        self._calls.append(call)
        self._runtime.state.llm_usages.append(
            LlmCallUsage(
                model=self._runtime.llm_client.connection.model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                duration_ms=0,
                retries=0,
                phase=phase,
                usage_reported=False,
                diagnostics=diagnostic,
            )
        )
        if streamed:
            self._streams.append(call)
        self._log("llm.call.started", call)
        return call

    def complete(self, call: _Call, response: ModelResponse) -> None:
        usage = response.usage
        details = usage.input_tokens_details
        cached = int(getattr(details, "cached_tokens", 0) or 0)
        cache_write = int(getattr(details, "cache_write_tokens", 0) or 0)
        current = self._runtime.state.llm_usages[call.usage_index]
        self._runtime.state.llm_usages[call.usage_index] = replace(
            current,
            prompt_tokens=int(usage.input_tokens or 0),
            completion_tokens=int(usage.output_tokens or 0),
            total_tokens=int(usage.total_tokens or 0),
            retries=max(0, usage.requests - 1),
            cached_input_tokens=cached,
            cache_write_input_tokens=cache_write,
            cache_usage_reported=bool(cached or cache_write),
            usage_reported=(
                current.usage_reported
                or response.raw_usage is not None
                or bool(usage.input_tokens or usage.output_tokens or usage.total_tokens)
            ),
        )
        diagnostic = call.diagnostics
        diagnostic.response_id = response.response_id
        diagnostic.provider_request_id = response.request_id
        if not diagnostic.streamed:
            for item in response.output:
                if isinstance(item, ResponseOutputMessage):
                    diagnostic.text_chars += sum(
                        len(part.text) for part in item.content if part.type == "output_text"
                    )
                elif isinstance(item, ResponseReasoningItem):
                    diagnostic.reasoning_chars += sum(len(part.text) for part in item.summary)
                    diagnostic.reasoning_chars += sum(len(part.text) for part in item.content or [])
                elif isinstance(item, ResponseFunctionToolCall):
                    diagnostic.tool_arguments_chars += len(item.arguments)
        self._end(call, "succeeded")
        if not diagnostic.streamed or diagnostic.terminal_event is not None:
            self._log_completed(call)

    def observe(self, event: Any) -> None:
        if event.type != "raw_response_event" or not self._streams:
            return
        call = self._streams[0]
        diagnostic = call.diagnostics
        data = event.data
        response = getattr(data, "response", None)
        if isinstance(response, Response):
            diagnostic.response_id = response.id
            if response.usage is not None:
                current = self._runtime.state.llm_usages[call.usage_index]
                self._runtime.state.llm_usages[call.usage_index] = replace(
                    current,
                    prompt_tokens=response.usage.input_tokens,
                    completion_tokens=response.usage.output_tokens,
                    total_tokens=response.usage.total_tokens,
                    cached_input_tokens=response.usage.input_tokens_details.cached_tokens,
                    cache_write_input_tokens=response.usage.input_tokens_details.cache_write_tokens,
                    cache_usage_reported=bool(
                        response.usage.input_tokens_details.cached_tokens
                        or response.usage.input_tokens_details.cache_write_tokens
                    ),
                    usage_reported=True,
                )
        if isinstance(data, ResponseTextDeltaEvent):
            diagnostic.text_chars += len(data.delta)
        elif isinstance(
            data, ResponseReasoningTextDeltaEvent | ResponseReasoningSummaryTextDeltaEvent
        ):
            diagnostic.reasoning_chars += len(data.delta)
        elif isinstance(data, ResponseFunctionCallArgumentsDeltaEvent):
            diagnostic.tool_arguments_chars += len(data.delta)
        else:
            if data.type in {
                "response.completed",
                "response.failed",
                "response.incomplete",
                "error",
                "response.error",
            }:
                diagnostic.terminal_event = data.type
                self._streams.popleft()
                if diagnostic.status != "running":
                    self._log_completed(call)
            return
        if not data.delta:
            return
        now = datetime.now(UTC).isoformat()
        diagnostic.last_chunk_at = now
        diagnostic.last_chunk_type = data.type
        diagnostic.content_event_count += 1
        if diagnostic.first_chunk_at is None:
            diagnostic.first_chunk_at = now
            self._log("llm.call.first_chunk", call)

    def finish(self, error: BaseException | None = None) -> None:
        """Close unfinished calls only; errors outside an LLM never relabel earlier calls."""
        for call in self._calls:
            if call.diagnostics.status == "running":
                status = "error"
                if isinstance(error, ModelTimeoutError):
                    status = "timeout"
                elif isinstance(error, asyncio.CancelledError):
                    status = "cancelled"
                call.diagnostics.error_type = type(error).__name__ if error is not None else None
                self._end(call, status)
            self._log_completed(call)
        self._streams.clear()

    def _end(self, call: _Call, status: str) -> None:
        call.diagnostics.status = status
        call.diagnostics.ended_at = datetime.now(UTC).isoformat()
        current = self._runtime.state.llm_usages[call.usage_index]
        self._runtime.state.llm_usages[call.usage_index] = replace(
            current, duration_ms=round((perf_counter() - call.started) * 1000, 2)
        )

    def _log_completed(self, call: _Call) -> None:
        if not call.logged:
            self._log("llm.call.completed", call)
            call.logged = True

    def _log(self, event: str, call: _Call) -> None:
        usage = self._runtime.state.llm_usages[call.usage_index]
        self._logger.info(
            event,
            extra={
                "workspace_id": self._runtime.request.workspace_id,
                "conversation_id": (
                    self._runtime.state.conversation_id or self._runtime.request.conversation_id
                ),
                "llm_connector_name": self._runtime.request.llm_connector_name,
                "model": usage.model,
                "phase": usage.phase,
                "duration_ms": usage.duration_ms,
                "usage_reported": usage.usage_reported,
                **asdict(call.diagnostics),
            },
        )


class SdkModelObservationHooks(RunHooks[Any]):
    """Native hooks shared by the tool loop and non-streaming compactor."""

    def __init__(self, observer: SdkModelCallObserver, *, phase: str, streamed: bool) -> None:
        self._observer = observer
        self._phase = phase
        self._streamed = streamed
        self._call: _Call | None = None

    async def on_llm_start(
        self, context: Any, agent: Any, system_prompt: Any, input_items: Any
    ) -> None:
        self._call = self._observer.start(phase=self._phase, streamed=self._streamed)

    async def on_llm_end(self, context: Any, agent: Any, response: ModelResponse) -> None:
        if self._call is not None:
            self._observer.complete(self._call, response)
