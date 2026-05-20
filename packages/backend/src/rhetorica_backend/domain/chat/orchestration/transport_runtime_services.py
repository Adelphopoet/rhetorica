"""Transport-specific runtime services for sync and streaming shells."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.chat import (
    ChatAskRequest,
    ChunkEvent,
    ErrorEvent,
    RuntimeStateEvent,
    StreamEvent,
    ThinkingEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    build_workspace_capability_surface,
    capability_trace_from_runtime,
)
from rhetorica_backend.domain.chat.orchestration.llm_runtime_contract import (
    LlmRuntimeContractResolver,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    PreparedRuntimeTurnInput,
    ToolCallRequest,
)
from rhetorica_backend.domain.chat.orchestration.runtime_loop_coordination import (
    RuntimeLoopInput,
    RuntimeLoopPreparedContent,
    RuntimeLoopTransportAdapter,
    ToolBatchExecutionHooks,
)
from rhetorica_backend.domain.chat.orchestration.session import OrchestrationSession
from rhetorica_backend.domain.chat.orchestration.strict_responses_runtime import (
    StrictResponsesRuntime,
)
from rhetorica_backend.domain.chat.orchestration.textual_tool_calls import (
    extract_textual_tool_call,
)
from rhetorica_backend.domain.chat.orchestration.turn_builder import (
    WorkspaceToolingSnapshot,
)
from rhetorica_backend.domain.chat.orchestration.turn_engine import (
    TurnEngineControl,
    TurnIterationContext,
)
from rhetorica_backend.domain.chat.orchestration.turn_policy import (
    resolve_turn_policy,
)

THINKING_TAG_PATTERN = re.compile(r"<(think|thinking)>(.*?)</\1>", flags=re.IGNORECASE | re.DOTALL)
JSON_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", flags=re.IGNORECASE | re.DOTALL
)


def _configured_for_responses_runtime(llm_client: Any) -> bool:
    """Use strict Responses runtime only when the connector explicitly configures it."""
    connection = getattr(llm_client, "connection", None)
    if connection is None:
        return True
    contract = LlmRuntimeContractResolver.resolve_native_responses_tooling(
        connection=connection,
        profile=None,
    )
    return contract.responses_endpoint_configured


class SyncLegacyOrchestrationService:
    """Owns the explicit sync legacy orchestration seam."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        session_factory: Any,
        turn_builder: Any,
        create_llm_client: Callable[[LlmConnectionConfig], Any],
        run_tool_loop: Callable[..., dict[str, Any]],
    ) -> None:
        self._logger = logger
        self._session_factory = session_factory
        self._turn_builder = turn_builder
        self._create_llm_client = create_llm_client
        self._run_tool_loop = run_tool_loop

    def execute(
        self,
        *,
        request: ChatAskRequest,
        conversation_id: str | None,
        warnings: list[str],
        active_session: OrchestrationSession | None,
    ) -> dict[str, Any]:
        session = active_session
        if session is None:
            session_request = request.model_copy(update={"conversation_id": conversation_id})
            session = self._session_factory.build(
                session_request,
                create_conversation_without_llm=True,
            )
        workspace_snapshot = session.workspace_snapshot
        capability_surface = session.capability_surface
        if shared_kernel.is_capabilities_question(request.question):
            return {
                "answer": shared_kernel.build_capabilities_answer(
                    has_db_connectors=workspace_snapshot.has_db_connectors,
                    metadata_configured=workspace_snapshot.metadata_configured,
                    rag_configured=workspace_snapshot.rag_configured,
                    confluence_configured=workspace_snapshot.confluence_configured,
                    jira_configured=workspace_snapshot.jira_configured,
                ),
                "sql": None,
                "target": None,
                "capability_trace": capability_trace_from_runtime(
                    available_tools=capability_surface.available_tools,
                    used_tools=[],
                    used_providers=[],
                    metadata_sources=[],
                    warnings=warnings,
                    has_execution=False,
                    degraded_capabilities=capability_surface.degraded_capabilities,
                    policy_decisions=capability_surface.policy_decisions,
                ).model_dump(mode="json"),
                "prompt_version": PromptBuilder.get_prompt_version(),
                "warnings": warnings,
            }
        llm_connection = session.llm_connection
        if llm_connection is None:
            warnings.append("llm_connector_missing")
            return {
                "answer": "LLM connector is not configured. Add it in admin.",
                "sql": None,
                "target": None,
                "capability_trace": capability_trace_from_runtime(
                    available_tools=capability_surface.available_tools,
                    used_tools=[],
                    used_providers=[],
                    metadata_sources=[],
                    warnings=warnings,
                    has_execution=False,
                    degraded_capabilities=capability_surface.degraded_capabilities,
                    policy_decisions=capability_surface.policy_decisions,
                ).model_dump(mode="json"),
                "prompt_version": PromptBuilder.get_prompt_version(),
                "warnings": warnings,
            }
        for warning in session.warnings:
            if warning not in warnings:
                warnings.append(warning)
        prepared_turn = self._turn_builder.prepare_turn(
            request=request,
            db_connectors=session.db_connectors,
            conversation_history=session.conversation_history,
            conversation_state=session.conversation_state,
            initial_warnings=warnings,
            requires_metadata_lookup=shared_kernel.requires_metadata_lookup(request),
            workspace_snapshot=workspace_snapshot,
        )
        return self._run_tool_loop(
            llm_client=self._create_llm_client(llm_connection),
            request=request,
            db_connectors=session.db_connectors,
            messages=prepared_turn.messages,
            tools=prepared_turn.tools,
            state=prepared_turn.state,
            tool_executor=prepared_turn.tool_executor,
            protocol_repair_enabled=prepared_turn.protocol_repair_enabled,
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
        self._strict_runtime = StrictResponsesRuntime(logger=logger)

    def run(
        self,
        *,
        llm_client: Any,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        state: Any,
        tool_executor: Any,
        protocol_repair_enabled: bool,
    ) -> dict[str, Any]:
        if _configured_for_responses_runtime(llm_client):
            _ = protocol_repair_enabled
            result = self._strict_runtime.run(
                llm_client=llm_client,
                request=request,
                db_connectors=db_connectors,
                messages=messages,
                tools=tools,
                state=state,
                tool_executor=tool_executor,
            )
            return result.llm_payload

        def before_iteration(
            context: TurnIterationContext,
        ) -> TurnEngineControl[dict[str, Any]] | None:
            self._logger.info(
                "LLM tool orchestration iteration %d/%d, warnings=%d, context_tables=%d, prompt_version=%s",
                context.iteration + 1,
                context.max_iterations,
                len(state.warnings),
                len(state.context_tables),
                state.prompt_version,
            )
            return None

        def prepare_response_content(raw_content: str) -> RuntimeLoopPreparedContent:
            StreamingResponseContentService.emit_thinking_diagnostics(raw_content)
            _, visible_content = extract_textual_tool_call(raw_content)
            return RuntimeLoopPreparedContent(
                raw_content=raw_content,
                completion_raw_content=visible_content,
                finalizable_content=visible_content.strip(),
            )

        def on_completion(
            completion: Any,
            _prepared_content: RuntimeLoopPreparedContent | None,
        ) -> None:
            if completion.completion_type == "success":
                self._logger.info(
                    "LLM tool orchestration completed successfully, generated_sql=%s, "
                    "prompt_version=%s, tool_calls=%d, parse_failures=%d, "
                    "schema_valid_first_pass=%d, schema_repaired=%d, schema_failed=%d, "
                    "preflight_pass=%d, preflight_fail=%d, preflight_uncertain=%d",
                    "yes" if state.generated_sql else "no",
                    state.prompt_version,
                    state.tool_call_count,
                    state.parse_failure_count,
                    state.schema_valid_first_pass,
                    state.schema_repaired,
                    state.schema_failed,
                    state.preflight_pass,
                    state.preflight_fail,
                    state.preflight_uncertain,
                )
            elif completion.completion_type == "loop_limit":
                self._logger.warning(
                    "LLM tool orchestration failed to complete within limit, partial_result=%s, "
                    "prompt_version=%s, tool_calls=%d, parse_failures=%d, "
                    "schema_valid_first_pass=%d, schema_repaired=%d, schema_failed=%d, "
                    "preflight_pass=%d, preflight_fail=%d, preflight_uncertain=%d",
                    "yes" if (state.generated_sql or state.execution) else "no",
                    state.prompt_version,
                    state.tool_call_count,
                    state.parse_failure_count,
                    state.schema_valid_first_pass,
                    state.schema_repaired,
                    state.schema_failed,
                    state.preflight_pass,
                    state.preflight_fail,
                    state.preflight_uncertain,
                )

        return self._execution_core.run_loop(
            runtime=RuntimeLoopInput(
                llm_client=llm_client,
                request=request,
                db_connectors=db_connectors,
                messages=messages,
                tools=tools,
                state=state,
                tool_executor=tool_executor,
                protocol_repair_enabled=protocol_repair_enabled,
            ),
            transport=RuntimeLoopTransportAdapter(
                before_iteration=before_iteration,
                prepare_response_content=prepare_response_content,
                on_llm_failure=lambda _exc, _state: None,
                on_completion=on_completion,
                adapt_payload=lambda payload: payload,
            ),
        )


class StreamingResponseContentService:
    """Owns streaming-specific content parsing and event shaping."""

    @classmethod
    def emit_thinking_diagnostics(
        cls,
        content: str,
        *,
        emit: Callable[..., None] = shared_kernel.emit_diagnostic_event,
    ) -> None:
        thinking_blocks, _ = cls.extract_thinking_blocks(content)
        for index, block in enumerate(thinking_blocks, start=1):
            emit(
                "llm.turn.thinking_block",
                block_index=index,
                content=block,
            )

    def process_content_for_thinking(
        self,
        content: str,
    ) -> list[ChunkEvent | ThinkingEvent]:
        events: list[ChunkEvent | ThinkingEvent] = []
        thinking_blocks, visible_content = self.extract_thinking_blocks(content)
        for block in thinking_blocks:
            events.append(ThinkingEvent(content=block))
        if visible_content.strip():
            events.append(ChunkEvent(content=visible_content.strip()))
        return events

    def thinking_events_from_content(self, content: str) -> list[ThinkingEvent]:
        thinking_blocks, _ = self.extract_thinking_blocks(content)
        return [ThinkingEvent(content=block) for block in thinking_blocks]

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
        error = result.get("error", "Unknown error")
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
        self._strict_runtime = StrictResponsesRuntime(logger=logger)

    def run_prepared(
        self,
        *,
        runtime: PreparedRuntimeTurnInput[Any],
        cancel_event: asyncio.Event | None,
        on_event: Callable[[StreamEvent], None] | None,
    ) -> Any:
        events: list[StreamEvent] = []
        state = runtime.state
        internal_optimizer_tool_call_count = 0
        active_internal_optimizer_calls: list[str] = []

        def record_event(event: StreamEvent) -> None:
            if on_event is None:
                events.append(event)
            if on_event is not None:
                on_event(event)

        def emit_internal_optimizer_start(arguments: dict[str, Any]) -> None:
            nonlocal internal_optimizer_tool_call_count
            internal_optimizer_tool_call_count += 1
            tool_call_id = f"internal_sql_optimizer_{internal_optimizer_tool_call_count}"
            active_internal_optimizer_calls.append(tool_call_id)
            record_event(
                ToolCallStartEvent(
                    tool_name="sql_optimization_advisor",
                    tool_call_id=tool_call_id,
                    arguments=arguments,
                )
            )

        def emit_internal_optimizer_end(trace: Any, summary: str) -> None:
            _ = trace
            tool_call_id = (
                active_internal_optimizer_calls.pop() if active_internal_optimizer_calls else None
            )
            record_event(
                ToolCallEndEvent(
                    tool_name="sql_optimization_advisor",
                    tool_call_id=tool_call_id,
                    result_summary=summary,
                    success=True,
                )
            )

        set_sql_optimizer_event_handlers = getattr(
            runtime.tool_executor,
            "set_sql_optimizer_event_handlers",
            None,
        )
        if callable(set_sql_optimizer_event_handlers):
            set_sql_optimizer_event_handlers(
                on_start=emit_internal_optimizer_start,
                on_end=emit_internal_optimizer_end,
            )

        if cancel_event and cancel_event.is_set():
            state.add_warning("cancelled")
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
        if _configured_for_responses_runtime(runtime.llm_client):
            strict_result = self._strict_runtime.run(
                llm_client=runtime.llm_client,
                request=runtime.request,
                db_connectors=runtime.db_connectors,
                messages=runtime.messages,
                tools=runtime.tools,
                state=runtime.state,
                tool_executor=runtime.tool_executor,
                emit=record_event,
            )
            return self._tool_loop_result_factory(
                payload=strict_result.llm_payload,
                events=events,
            )

        def cancelled_payload() -> dict[str, Any]:
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
            context: TurnIterationContext,
        ) -> TurnEngineControl[Any] | None:
            if cancel_event and cancel_event.is_set():
                self._logger.info("Tool loop cancelled at iteration %d", context.iteration)
                return TurnEngineControl(
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
            record_event(
                ErrorEvent(
                    code="llm_request_failed",
                    message="LLM request failed. Check connector URL, TLS settings, and API key.",
                    details={"type": type(exc).__name__},
                )
            )

        def prepare_response_content(raw_content: str) -> RuntimeLoopPreparedContent:
            self._content_service.emit_thinking_diagnostics(raw_content)
            for event in self._content_service.thinking_events_from_content(raw_content):
                record_event(event)
            _, visible_content = extract_textual_tool_call(raw_content)
            _, stripped_visible_content = self._content_service.extract_thinking_blocks(
                visible_content
            )
            return RuntimeLoopPreparedContent(
                raw_content=raw_content,
                completion_raw_content=visible_content,
                finalizable_content=self._content_service.strip_json_markdown_fence(
                    stripped_visible_content
                ).strip(),
            )

        def on_completion(
            completion: Any,
            prepared_content: RuntimeLoopPreparedContent | None,
        ) -> None:
            if completion.completion_type != "success" or prepared_content is None:
                return
            visible_chunk = self._content_service.visible_chunk_event(
                prepared_content.completion_raw_content
            )
            if visible_chunk is not None:
                record_event(visible_chunk)
            self._logger.info("LLM tool orchestration completed successfully")

        def before_tool_batch() -> Any | None:
            if cancel_event and cancel_event.is_set():
                self._logger.info("Tool execution cancelled")
                return self._tool_loop_result_factory(
                    payload=cancelled_payload(),
                    events=events,
                )
            return None

        def on_tool_call_start(tool_request: ToolCallRequest) -> None:
            record_event(
                ToolCallStartEvent(
                    tool_name=tool_request.tool_name,
                    tool_call_id=tool_request.tool_call_id,
                    arguments=tool_request.arguments,
                )
            )

        def on_tool_call_end(
            tool_request: ToolCallRequest,
            tool_result: dict[str, Any],
        ) -> None:
            # Resolve success: prefer explicit 'ok' field, fall back to checking 'status'.
            success = tool_result.get("ok", tool_result.get("status") == "succeeded")
            record_event(
                ToolCallEndEvent(
                    tool_name=tool_request.tool_name,
                    tool_call_id=tool_request.tool_call_id,
                    result_summary=self._content_service.summarize_tool_result(tool_result),
                    success=success,
                )
            )

        def on_runtime_state_update(updated_state: Any) -> None:
            analysis_plan = getattr(updated_state, "analysis_plan", None)
            checkpoint = getattr(updated_state, "checkpoint", None)
            worker_results = getattr(updated_state, "worker_results", [])
            if analysis_plan is None and checkpoint is None and not worker_results:
                return
            record_event(
                RuntimeStateEvent(
                    analysis_plan=(
                        analysis_plan.model_dump(mode="json") if analysis_plan is not None else None
                    ),
                    worker_results=[
                        result.compact_payload()
                        for result in worker_results[-5:]
                        if hasattr(result, "compact_payload")
                    ],
                    checkpoint_digest=(
                        checkpoint.digest_payload() if checkpoint is not None else None
                    ),
                )
            )

        return self._execution_core.run_loop(
            runtime=RuntimeLoopInput(
                llm_client=runtime.llm_client,
                request=runtime.request,
                db_connectors=runtime.db_connectors,
                messages=runtime.messages,
                tools=runtime.tools,
                state=runtime.state,
                tool_executor=runtime.tool_executor,
                protocol_repair_enabled=runtime.protocol_repair_enabled,
            ),
            transport=RuntimeLoopTransportAdapter(
                before_iteration=before_iteration,
                prepare_response_content=prepare_response_content,
                on_llm_failure=on_llm_failure,
                on_completion=on_completion,
                adapt_payload=lambda payload: self._tool_loop_result_factory(
                    payload=payload,
                    events=events,
                ),
                before_tool_batch=before_tool_batch,
                on_runtime_state_update=on_runtime_state_update,
                tool_batch_hooks=ToolBatchExecutionHooks(
                    on_tool_call_start=on_tool_call_start,
                    on_tool_call_end=on_tool_call_end,
                    on_runtime_state_update=on_runtime_state_update,
                ),
            ),
        )


class StreamingLegacyRuntimeService:
    """Owns the explicit streaming legacy compatibility seam."""

    def __init__(
        self,
        *,
        sql_router: Any,
        turn_builder: Any,
        create_llm_client: Callable[[LlmConnectionConfig], Any],
        run_prepared_tool_loop_and_collect: Callable[..., Any],
        tool_loop_result_factory: Callable[..., Any],
        state_factory: Callable[[], Any],
    ) -> None:
        self._sql_router = sql_router
        self._turn_builder = turn_builder
        self._create_llm_client = create_llm_client
        self._run_prepared_tool_loop_and_collect = run_prepared_tool_loop_and_collect
        self._tool_loop_result_factory = tool_loop_result_factory
        self._state_factory = state_factory

    @staticmethod
    def workspace_snapshot_from_flags(
        *,
        has_db_connectors: bool,
        metadata_configured: bool,
        metadata_connector_names: list[str],
        rag_configured: bool,
        confluence_configured: bool,
        confluence_connector_names: list[str],
        jira_configured: bool,
        jira_connector_names: list[str],
    ) -> WorkspaceToolingSnapshot:
        return WorkspaceToolingSnapshot(
            has_db_connectors=has_db_connectors,
            metadata_configured=metadata_configured,
            metadata_connector_names=list(metadata_connector_names),
            rag_configured=rag_configured,
            confluence_configured=confluence_configured,
            confluence_connector_names=list(confluence_connector_names),
            jira_configured=jira_configured,
            jira_connector_names=list(jira_connector_names),
        )

    def collect_sync(
        self,
        session: OrchestrationSession | None = None,
        *,
        llm_connection: LlmConnectionConfig | None = None,
        request: ChatAskRequest | None = None,
        db_connectors: list[ConnectorItem] | None = None,
        metadata_configured: bool | None = None,
        metadata_connector_names: list[str] | None = None,
        rag_configured: bool | None = None,
        confluence_configured: bool | None = None,
        confluence_connector_names: list[str] | None = None,
        jira_configured: bool = False,
        jira_connector_names: list[str] | None = None,
        cancel_event: asyncio.Event | None = None,
        on_event: Callable[[StreamEvent], None] | None = None,
    ) -> Any:
        if session is None:
            if request is None or db_connectors is None:
                raise TypeError
            workspace_snapshot = self.workspace_snapshot_from_flags(
                has_db_connectors=bool(db_connectors),
                metadata_configured=bool(metadata_configured),
                metadata_connector_names=list(metadata_connector_names or []),
                rag_configured=bool(rag_configured),
                confluence_configured=bool(confluence_configured),
                confluence_connector_names=list(confluence_connector_names or []),
                jira_configured=bool(jira_configured),
                jira_connector_names=list(jira_connector_names or []),
            )
            policy = resolve_turn_policy(
                request=request,
                has_db_connectors=workspace_snapshot.has_db_connectors,
                metadata_configured=workspace_snapshot.metadata_configured,
                rag_configured=workspace_snapshot.rag_configured,
                confluence_configured=workspace_snapshot.confluence_configured,
                jira_configured=workspace_snapshot.jira_configured,
                sql_router=self._sql_router,
                requires_metadata_lookup=shared_kernel.requires_metadata_lookup(request),
            )
            session = OrchestrationSession(
                request=request,
                conversation_id=request.conversation_id,
                llm_connection=llm_connection,
                db_connectors=list(db_connectors),
                workspace_snapshot=workspace_snapshot,
                capability_surface=build_workspace_capability_surface(
                    has_db_connectors=workspace_snapshot.has_db_connectors,
                    metadata_configured=workspace_snapshot.metadata_configured,
                    rag_configured=workspace_snapshot.rag_configured,
                    confluence_configured=workspace_snapshot.confluence_configured,
                    jira_configured=workspace_snapshot.jira_configured,
                    policy=policy,
                    run_sql_requested=request.run_sql,
                ),
                conversation_history=[],
                warnings=[],
            )

        request = session.request
        workspace_snapshot = session.workspace_snapshot
        capability_surface = session.capability_surface
        if shared_kernel.is_capabilities_question(request.question):
            return self._tool_loop_result_factory(
                payload={
                    "answer": shared_kernel.build_capabilities_answer(
                        has_db_connectors=workspace_snapshot.has_db_connectors,
                        metadata_configured=workspace_snapshot.metadata_configured,
                        rag_configured=workspace_snapshot.rag_configured,
                        confluence_configured=workspace_snapshot.confluence_configured,
                        jira_configured=workspace_snapshot.jira_configured,
                    ),
                    "sql": None,
                    "target": None,
                    "selected_database_connector": None,
                    "metadata_sources": [],
                    "context_tables": [],
                    "claims": [],
                    "evidence": [],
                    "claim_evidence_links": [],
                    "uncertainty_flags": [],
                    "execution": None,
                    "visualization": None,
                    "capability_trace": capability_trace_from_runtime(
                        available_tools=capability_surface.available_tools,
                        used_tools=[],
                        used_providers=[],
                        metadata_sources=[],
                        warnings=list(session.warnings),
                        has_execution=False,
                        degraded_capabilities=capability_surface.degraded_capabilities,
                        policy_decisions=capability_surface.policy_decisions,
                    ).model_dump(mode="json"),
                    "prompt_version": PromptBuilder.get_prompt_version(),
                    "warnings": list(session.warnings),
                },
                events=[],
            )
        prepared_turn = self._turn_builder.prepare_turn(
            request=request,
            db_connectors=session.db_connectors,
            conversation_history=session.conversation_history,
            conversation_state=session.conversation_state,
            initial_warnings=session.warnings,
            requires_metadata_lookup=shared_kernel.requires_metadata_lookup(request),
            workspace_snapshot=workspace_snapshot,
            state_factory=self._state_factory,
        )
        runtime = PreparedRuntimeTurnInput(
            session=session,
            request=session.request,
            db_connectors=session.db_connectors,
            messages=prepared_turn.messages,
            tools=prepared_turn.tools,
            state=prepared_turn.state,
            tool_executor=prepared_turn.tool_executor,
            protocol_repair_enabled=prepared_turn.protocol_repair_enabled,
            llm_client=self._create_llm_client(session.llm_connection),
        )
        return self._run_prepared_tool_loop_and_collect(
            runtime=runtime,
            cancel_event=cancel_event,
            on_event=on_event,
        )
