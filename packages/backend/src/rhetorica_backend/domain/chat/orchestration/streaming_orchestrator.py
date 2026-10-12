"""Streaming chat orchestrator - SSE event generation."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.chat import (
    ChatAskRequest,
    ChunkEvent,
    ErrorEvent,
    StreamEvent,
    ThinkingEvent,
)
from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.chat_runtime_orchestrator import (
    ChatRuntimeOrchestrator,
)
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    PreparedRuntimeTurnInput,
    RuntimeExecutionResult,
    StreamingRuntimeTransportArtifact,
)
from rhetorica_backend.domain.chat.orchestration.orchestration_service_bundles import (
    build_streaming_orchestration_service_bundle,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.tool_executor import (
    ToolExecutor,
)
from rhetorica_backend.domain.chat.orchestration.tool_file_capability_service import (
    ToolFileCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.transport_runtime_services import (
    StreamingResponseContentService,
    StreamingRuntimeLoopService,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.documentation import DocumentationRetrievalService
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.domain.results.visualization_planner import (
    VisualizationPlanner,
)
from rhetorica_backend.infra.postgres.airflow_connector_repository import (
    AirflowConnectorRepository,
)
from rhetorica_backend.infra.postgres.documentation_connector_repository import (
    DocumentationConnectorRepository,
)
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)
from rhetorica_backend.infra.postgres.kubernetes_connector_repository import (
    KubernetesConnectorRepository,
)
from rhetorica_backend.infra.postgres.llm_connector_repository import (
    LlmConnectorRepository,
)
from rhetorica_backend.infra.postgres.llm_credential_binding_repository import (
    LlmCredentialBindingRepository,
)
from rhetorica_backend.infra.postgres.mcp_connector_repository import (
    McpConnectorRepository,
)
from rhetorica_backend.infra.postgres.victoriametrics_connector_repository import (
    VictoriaMetricsConnectorRepository,
)
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.infra.llm.capability_registry import LlmCapabilityRegistry

logger = logging.getLogger(__name__)
THINKING_TAG_PATTERN = re.compile(r"<(think|thinking)>(.*?)</\1>", flags=re.IGNORECASE | re.DOTALL)
JSON_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", flags=re.IGNORECASE | re.DOTALL
)


@dataclass
class StreamingToolState(ToolOrchestrationState):
    """Extended state for streaming tool orchestration."""

    cancelled: bool = False


@dataclass
class ToolLoopResult:
    """Result from streaming tool loop execution."""

    payload: dict[str, Any]
    events: list[StreamEvent] = field(default_factory=list)


class StreamingChatOrchestrator:
    """
    Streaming orchestrator for chat interactions with SSE event generation.

    Coordinates:
    - LLM client for streaming completions
    - Metadata resolver and fetcher for table discovery
    - SQL router for query execution
    - Conversation manager for history
    - SSE event generation for real-time updates
    """

    def __init__(
        self,
        llm_connector_repository: LlmConnectorRepository,
        sql_router: SqlRouter,
        conversation_manager: ConversationManager,
        db_connector_repository: Any,
        rag_retriever: RagInsightRetriever | None = None,
        documentation_connector_repository: DocumentationConnectorRepository | None = None,
        documentation_retrieval_service: DocumentationRetrievalService | None = None,
        gitlab_connector_repository: GitLabConnectorRepository | None = None,
        airflow_connector_repository: AirflowConnectorRepository | None = None,
        kubernetes_connector_repository: KubernetesConnectorRepository | None = None,
        victoriametrics_connector_repository: VictoriaMetricsConnectorRepository | None = None,
        mcp_connector_repository: McpConnectorRepository | None = None,
        workspace_skill_repository: WorkspaceSkillRepository | None = None,
        visualization_planner: VisualizationPlanner | None = None,
        capability_registry: LlmCapabilityRegistry | None = None,
        file_capability_service: ToolFileCapabilityService | None = None,
        agent_session_factory: Callable[[str], Any] | None = None,
        llm_credential_binding_repository: LlmCredentialBindingRepository | None = None,
    ):
        self._llm_connector_repository = llm_connector_repository
        self._sql_router = sql_router
        self._conversation_manager = conversation_manager
        self._db_connector_repository = db_connector_repository
        self._rag_retriever = rag_retriever
        self._documentation_connector_repository = documentation_connector_repository
        self._documentation_retrieval_service = documentation_retrieval_service
        self._gitlab_connector_repository = gitlab_connector_repository
        self._airflow_connector_repository = airflow_connector_repository
        self._kubernetes_connector_repository = kubernetes_connector_repository
        self._victoriametrics_connector_repository = victoriametrics_connector_repository
        self._mcp_connector_repository = mcp_connector_repository
        self._workspace_skill_repository = workspace_skill_repository
        self._file_capability_service = file_capability_service
        self._logger = logging.getLogger(__name__)
        self._detached_producers: set[asyncio.Task[None]] = set()
        self._final_payload_builder = FinalPayloadBuilderService(
            normalize_answer=lambda *, answer, state: answer,
        )
        self._runtime = ChatRuntimeOrchestrator.build(
            llm_connector_repository=self._llm_connector_repository,
            sql_router=self._sql_router,
            conversation_manager=self._conversation_manager,
            db_connector_repository=self._db_connector_repository,
            rag_retriever=self._rag_retriever,
            documentation_connector_repository=self._documentation_connector_repository,
            documentation_retrieval_service=self._documentation_retrieval_service,
            gitlab_connector_repository=self._gitlab_connector_repository,
            airflow_connector_repository=self._airflow_connector_repository,
            kubernetes_connector_repository=self._kubernetes_connector_repository,
            victoriametrics_connector_repository=self._victoriametrics_connector_repository,
            mcp_connector_repository=self._mcp_connector_repository,
            workspace_skill_repository=self._workspace_skill_repository,
            visualization_planner=visualization_planner,
            tool_executor_factory=lambda **kwargs: ToolExecutor(
                **kwargs, file_capability_service=self._file_capability_service
            ),
            file_tools_enabled=self._file_capability_service is not None,
            capability_registry=capability_registry,
            agent_session_factory=agent_session_factory,
            llm_credential_binding_repository=llm_credential_binding_repository,
        )
        self._visualization_planner = self._runtime.visualization_planner
        self._analysis_document_builder = self._runtime.analysis_document_builder
        self._turn_builder = self._runtime.turn_builder
        self._session_factory = self._runtime.session_factory
        self._response_assembler = self._runtime.response_assembler
        self._history_persistence_service = self._runtime.history_persistence_service
        self._history_persistence_policy = self._runtime.history_persistence_policy
        bundle: Any = build_streaming_orchestration_service_bundle(
            logger=self._logger,
            final_payload_builder=self._final_payload_builder,
            session_factory=self._session_factory,
            turn_builder=self._turn_builder,
            response_assembler=self._response_assembler,
            create_llm_client=lambda connection: self._create_llm_client(connection),
            history_persistence_policy=self._history_persistence_policy,
            history_persistence_service=self._history_persistence_service,
            transport_finalization_input_factory=(
                self._runtime.transport_finalization_input_factory
            ),
        )
        self._tool_execution_policy_service = bundle.tool_execution_policy
        self._session_turn_flow_service = bundle.session_turn_flow_service
        self._transport_finalization_service = bundle.transport_finalization_service
        self._transport_finalization_input_factory = bundle.transport_finalization_input_factory
        self._execution_core = bundle.execution_core
        self._streaming_content_service = StreamingResponseContentService()
        self._streaming_runtime_loop_service = StreamingRuntimeLoopService(
            logger=self._logger,
            execution_core=self._execution_core,
            content_service=self._streaming_content_service,
            tool_loop_result_factory=ToolLoopResult,
        )

    def _create_llm_client(self, connection: LlmConnectionConfig) -> LlmClient:
        """Create LLM client from connection."""
        return LlmClient(connection)

    async def stream_ask(
        self,
        request: ChatAskRequest,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Stream chat response with SSE events.

        Args:
            request: Chat request.
            cancel_event: Optional cancellation event.

        Yields:
            StreamEvent instances for SSE.
        """
        queue: asyncio.Queue[StreamEvent | object] = asyncio.Queue()
        sentinel = object()

        def emit(event: StreamEvent) -> None:
            queue.put_nowait(event)

        def finish() -> None:
            queue.put_nowait(sentinel)

        async def runner() -> None:
            try:
                await self._run_stream_session(
                    request=request,
                    cancel_event=cancel_event,
                    emit=emit,
                )
            except Exception as exc:
                self._logger.exception("Stream error")
                emit(
                    ErrorEvent(
                        code="stream_error",
                        message=str(exc),
                        details={"type": type(exc).__name__},
                    )
                )
            finally:
                finish()

        producer_task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                yield item  # type: ignore[misc]
        except asyncio.CancelledError:
            self._logger.info("Stream transport disconnected for request")
            raise
        finally:
            if not producer_task.done():
                self._detached_producers.add(producer_task)
                producer_task.add_done_callback(self._observe_detached_producer)
            else:
                with suppress(asyncio.CancelledError, Exception):
                    await producer_task

    def _observe_detached_producer(self, task: asyncio.Task[None]) -> None:
        self._detached_producers.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            self._logger.info("Detached chat turn was cancelled explicitly")
        except Exception:
            self._logger.exception("Detached chat turn failed")

    async def _run_stream_session(
        self,
        request: ChatAskRequest,
        cancel_event: asyncio.Event | None = None,
        emit: Callable[[StreamEvent], None] | None = None,
    ) -> None:
        """Run one orchestration session natively in the current event loop."""
        self._logger.info(
            "chat.stream.started",
            extra={
                "workspace_id": request.workspace_id,
                "conversation_id": request.conversation_id,
                "llm_connector_name": request.llm_connector_name,
                "database_connector_name": request.database_connector_name,
                "run_sql": request.run_sql,
                "question": request.question,
            },
        )
        shared_kernel.emit_diagnostic_event(
            "chat.stream.started",
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
            llm_connector_name=request.llm_connector_name,
            database_connector_name=request.database_connector_name,
            run_sql=request.run_sql,
            question=request.question,
        )
        try:
            finalized = await self._execution_core.execute_session_async(
                request=request,
                create_conversation_without_llm=False,
                state_factory=StreamingToolState,
                execute_runtime=lambda runtime: self._execute_stream_runtime_flow(
                    runtime=runtime,
                    cancel_event=cancel_event,
                    emit=emit,
                ),
            )
        finally:
            if self._file_capability_service is not None:
                await asyncio.to_thread(self._file_capability_service.close_sessions)
        for event in finalized.events:
            self._emit(emit, event)

        diagnostics = finalized.diagnostics
        if diagnostics is None:
            return

        self._logger.info(
            "chat.stream.completed",
            extra=diagnostics.to_log_fields(workspace_id=request.workspace_id),
        )
        shared_kernel.emit_diagnostic_event(
            "chat.stream.completed",
            **diagnostics.to_log_fields(workspace_id=request.workspace_id),
        )

    async def _execute_stream_runtime_flow(
        self,
        *,
        runtime: PreparedRuntimeTurnInput[StreamingToolState],
        cancel_event: asyncio.Event | None,
        emit: Callable[[StreamEvent], None] | None,
    ) -> RuntimeExecutionResult:
        runtime_result = await self._run_prepared_tool_loop_and_collect(
            runtime=runtime,
            cancel_event=cancel_event,
            on_event=emit,
        )
        return RuntimeExecutionResult(
            llm_payload=runtime_result.payload,
            transport_artifact=StreamingRuntimeTransportArtifact(
                events=list(runtime_result.events)
            ),
        )

    async def _run_prepared_tool_loop_and_collect(
        self,
        *,
        runtime: PreparedRuntimeTurnInput[StreamingToolState],
        cancel_event: asyncio.Event | None,
        on_event: Callable[[StreamEvent], None] | None,
    ) -> ToolLoopResult:
        return cast(
            ToolLoopResult,
            await self._streaming_runtime_loop_service.run_prepared(
                runtime=runtime,
                cancel_event=cancel_event,
                on_event=on_event,
            ),
        )

    @staticmethod
    def _emit(
        emitter: Callable[[StreamEvent], None] | None,
        event: StreamEvent,
    ) -> None:
        if emitter is not None:
            emitter(event)

    @classmethod
    def _record_event(
        cls,
        events: list[StreamEvent],
        event: StreamEvent,
        emitter: Callable[[StreamEvent], None] | None,
    ) -> None:
        if emitter is None:
            events.append(event)
        cls._emit(emitter, event)

    def _process_content_for_thinking(self, content: str) -> list[ChunkEvent | ThinkingEvent]:
        return self._streaming_content_service.process_content_for_thinking(content)

    def _thinking_events_from_content(self, content: str) -> list[ThinkingEvent]:
        return self._streaming_content_service.thinking_events_from_content(content)

    def _visible_chunk_event(self, content: str) -> ChunkEvent | None:
        return self._streaming_content_service.visible_chunk_event(content)

    def _process_final_content(self, content: str) -> list[ChunkEvent | ThinkingEvent]:
        return self._streaming_content_service.process_final_content(content)

    @staticmethod
    def _summarize_tool_result(result: dict[str, Any]) -> str:
        return StreamingResponseContentService.summarize_tool_result(result)

    @staticmethod
    def _parse_json_payload(content: str) -> tuple[dict[str, Any], bool]:
        return StreamingResponseContentService.parse_json_payload(content)

    @staticmethod
    def _extract_thinking_blocks(content: str) -> tuple[list[str], str]:
        return StreamingResponseContentService.extract_thinking_blocks(content)

    @staticmethod
    def _strip_json_markdown_fence(content: str) -> str:
        return StreamingResponseContentService.strip_json_markdown_fence(content)
