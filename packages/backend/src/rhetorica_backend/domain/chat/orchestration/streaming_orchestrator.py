"""Streaming chat orchestrator - SSE event generation."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.chat import (
    ChatAskRequest,
    ChunkEvent,
    ErrorEvent,
    StreamEvent,
    ThinkingEvent,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.metadata import (
    MetadataCatalogSearchService,
    MetadataFetcher,
    MetadataRankingService,
    MetadataSemanticGroundingService,
    MetadataTableLoader,
)
from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.chat_runtime_orchestrator import (
    ChatRuntimeOrchestrator,
)
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
    RecoveryFinalizationService,
)
from rhetorica_backend.domain.chat.orchestration.legacy_compatibility import (
    LegacyStreamingRuntimeCompatibilityAdapter,
)
from rhetorica_backend.domain.chat.orchestration.llm_finalization_policy import (
    LlmFinalizationPolicy,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    PreparedRuntimeTurnInput,
    RuntimeExecutionResult,
    StopToolResultDecision,
)
from rhetorica_backend.domain.chat.orchestration.orchestration_service_bundles import (
    build_streaming_orchestration_service_bundle,
    build_tool_execution_policy,
)
from rhetorica_backend.domain.chat.orchestration.session import (
    OrchestrationSession,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_policy import (
    ToolExecutionPolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_executor import (
    ToolExecutor,
)
from rhetorica_backend.domain.chat.orchestration.transport_runtime_services import (
    StreamingLegacyRuntimeService,
    StreamingResponseContentService,
    StreamingRuntimeLoopService,
)
from rhetorica_backend.domain.chat.orchestration.turn_builder import (
    WorkspaceToolingSnapshot,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.domain.results.visualization_planner import (
    VisualizationPlanner,
)
from rhetorica_backend.infra.postgres.confluence_connector_repository import (
    ConfluenceConnectorRepository,
)
from rhetorica_backend.infra.postgres.jira_connector_repository import (
    JiraConnectorRepository,
)
from rhetorica_backend.infra.postgres.llm_connector_repository import (
    LlmConnectorRepository,
)
from rhetorica_backend.infra.postgres.openmetadata_connector_repository import (
    OpenMetadataConnectorRepository,
)
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.infra.llm.capability_registry import LlmCapabilityRegistry

logger = logging.getLogger(__name__)
THINKING_TAG_PATTERN = re.compile(
    r"<(think|thinking)>(.*?)</\1>", flags=re.IGNORECASE | re.DOTALL
)
JSON_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", flags=re.IGNORECASE | re.DOTALL
)
MAX_PROTOCOL_REPAIR_ATTEMPTS = 8


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
        openmetadata_connector_repository: OpenMetadataConnectorRepository,
        metadata_fetcher: MetadataFetcher,
        sql_router: SqlRouter,
        conversation_manager: ConversationManager,
        db_connector_repository: Any,
        rag_retriever: RagInsightRetriever | None = None,
        confluence_connector_repository: ConfluenceConnectorRepository | None = None,
        jira_connector_repository: JiraConnectorRepository | None = None,
        workspace_skill_repository: WorkspaceSkillRepository | None = None,
        visualization_planner: VisualizationPlanner | None = None,
        metadata_table_loader: MetadataTableLoader | None = None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None = None,
        metadata_ranking_service: MetadataRankingService | None = None,
        metadata_semantic_grounding_service: MetadataSemanticGroundingService | None = None,
        capability_registry: LlmCapabilityRegistry | None = None,
    ):
        self._llm_connector_repository = llm_connector_repository
        self._openmetadata_connector_repository = openmetadata_connector_repository
        self._sql_router = sql_router
        self._conversation_manager = conversation_manager
        self._db_connector_repository = db_connector_repository
        self._rag_retriever = rag_retriever
        self._confluence_connector_repository = confluence_connector_repository
        self._jira_connector_repository = jira_connector_repository
        self._workspace_skill_repository = workspace_skill_repository
        self._logger = logging.getLogger(__name__)
        self._final_payload_builder = FinalPayloadBuilderService(
            normalize_answer=lambda *, answer, state: answer,
        )
        bootstrap_finalization_policy = self._build_llm_finalization_policy()
        self._recovery_finalization_service = RecoveryFinalizationService(
            finalization_policy=bootstrap_finalization_policy,
            emit_diagnostic_event=shared_kernel.emit_diagnostic_event,
        )
        self._runtime = ChatRuntimeOrchestrator.build(
            llm_connector_repository=self._llm_connector_repository,
            openmetadata_connector_repository=self._openmetadata_connector_repository,
            metadata_fetcher=metadata_fetcher,
            sql_router=self._sql_router,
            conversation_manager=self._conversation_manager,
            db_connector_repository=self._db_connector_repository,
            rag_retriever=self._rag_retriever,
            confluence_connector_repository=self._confluence_connector_repository,
            jira_connector_repository=self._jira_connector_repository,
            workspace_skill_repository=self._workspace_skill_repository,
            visualization_planner=visualization_planner,
            metadata_table_loader=metadata_table_loader,
            metadata_catalog_search_service=metadata_catalog_search_service,
            metadata_ranking_service=metadata_ranking_service,
            metadata_semantic_grounding_service=metadata_semantic_grounding_service,
            tool_executor_factory=lambda **kwargs: ToolExecutor(**kwargs),
            capability_registry=capability_registry,
        )
        self._metadata_table_loader = self._runtime.metadata_table_loader
        self._metadata_catalog_search_service = self._runtime.metadata_catalog_search_service
        self._metadata_ranking_service = self._runtime.metadata_ranking_service
        self._metadata_semantic_grounding_service = (
            self._runtime.metadata_semantic_grounding_service
        )
        self._visualization_planner = self._runtime.visualization_planner
        self._analysis_document_builder = self._runtime.analysis_document_builder
        self._turn_builder = self._runtime.turn_builder
        self._session_factory = self._runtime.session_factory
        self._response_assembler = self._runtime.response_assembler
        self._history_persistence_service = self._runtime.history_persistence_service
        self._history_persistence_policy = self._runtime.history_persistence_policy
        bundle = build_streaming_orchestration_service_bundle(
            logger=self._logger,
            final_payload_builder=self._final_payload_builder,
            recovery_finalization_service=self._recovery_finalization_service,
            session_factory=self._session_factory,
            turn_builder=self._turn_builder,
            response_assembler=self._response_assembler,
            create_llm_client=lambda connection: self._create_llm_client(connection),
            history_persistence_policy=self._history_persistence_policy,
            history_persistence_service=self._history_persistence_service,
            transport_finalization_input_factory=(
                self._runtime.transport_finalization_input_factory
            ),
            incomplete_tool_orchestration_payload=(
                self._incomplete_tool_orchestration_payload
            ),
        )
        self._llm_finalization_policy = bundle.llm_finalization_policy
        self._tool_execution_policy_service = bundle.tool_execution_policy
        self._session_turn_flow_service = bundle.session_turn_flow_service
        self._transport_finalization_service = bundle.transport_finalization_service
        self._transport_finalization_input_factory = (
            bundle.transport_finalization_input_factory
        )
        self._execution_core = bundle.execution_core
        self._streaming_content_service = StreamingResponseContentService()
        self._streaming_runtime_loop_service = StreamingRuntimeLoopService(
            logger=self._logger,
            execution_core=self._execution_core,
            content_service=self._streaming_content_service,
            tool_loop_result_factory=ToolLoopResult,
        )
        self._streaming_legacy_runtime_service = StreamingLegacyRuntimeService(
            sql_router=self._sql_router,
            turn_builder=self._turn_builder,
            create_llm_client=lambda connection: self._create_llm_client(connection),
            run_prepared_tool_loop_and_collect=(
                self._streaming_runtime_loop_service.run_prepared
            ),
            tool_loop_result_factory=ToolLoopResult,
            state_factory=StreamingToolState,
        )
        self._legacy_runtime_compatibility_adapter = (
            self._build_legacy_runtime_compatibility_adapter()
        )

    def _create_llm_client(self, connection: LlmConnectionConfig) -> LlmClient:
        """Create LLM client from connection."""
        return LlmClient(connection)

    def _build_llm_finalization_policy(self) -> LlmFinalizationPolicy:
        return LlmFinalizationPolicy(
            logger=self._logger,
            build_finalizer_context=shared_kernel.build_finalizer_context,
            build_final_payload_from_parsed=(
                self._final_payload_builder.build_final_payload_from_parsed
            ),
            fallback_final_payload=self._final_payload_builder.fallback_final_payload,
            deterministic_finalizer_answer=shared_kernel.deterministic_finalizer_answer,
            execution_recovery_prompt=shared_kernel.execution_recovery_prompt,
            extract_message_content=shared_kernel.extract_message_content,
            has_needs_user_clarification_flag=(
                shared_kernel.has_needs_user_clarification_flag
            ),
            is_acceptable_final_payload=shared_kernel.is_acceptable_final_payload,
            merge_uncertainty_flags=shared_kernel.merge_uncertainty_flags,
            preserve_raw_markdown_answer=shared_kernel.preserve_raw_markdown_answer,
            extract_uncertainty_flags=shared_kernel.extract_uncertainty_flags,
            successful_execution_attempts=shared_kernel.successful_execution_attempts,
            looks_like_nonfinal_raw_payload=shared_kernel.looks_like_nonfinal_raw_payload,
            to_optional_str=shared_kernel.to_optional_str,
            strip_thinking_tags=shared_kernel.strip_thinking_tags,
        )

    def _finalization_policy(self) -> LlmFinalizationPolicy:
        return self._llm_finalization_policy

    @classmethod
    def _build_tool_execution_policy(cls) -> ToolExecutionPolicy:
        _ = cls
        return build_tool_execution_policy()

    def _tool_execution_policy(self) -> ToolExecutionPolicy:
        return self._tool_execution_policy_service

    def _build_legacy_runtime_compatibility_adapter(
        self,
    ) -> LegacyStreamingRuntimeCompatibilityAdapter:
        return LegacyStreamingRuntimeCompatibilityAdapter(
            default_collect_tool_loop=_DEFAULT_STREAM_RUN_TOOL_LOOP_AND_COLLECT,
        )

    @staticmethod
    def _workspace_snapshot_from_flags(
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
        return StreamingLegacyRuntimeService.workspace_snapshot_from_flags(
            has_db_connectors=has_db_connectors,
            metadata_configured=metadata_configured,
            metadata_connector_names=metadata_connector_names,
            rag_configured=rag_configured,
            confluence_configured=confluence_configured,
            confluence_connector_names=confluence_connector_names,
            jira_configured=jira_configured,
            jira_connector_names=jira_connector_names,
        )

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
        loop = asyncio.get_running_loop()

        def emit(event: StreamEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def finish() -> None:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        async def runner() -> None:
            try:
                await asyncio.to_thread(
                    lambda: asyncio.run(
                        self._run_stream_session(
                            request=request,
                            cancel_event=cancel_event,
                            emit=emit,
                        )
                    )
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
            self._logger.info("Stream cancelled for request")
            if cancel_event is not None:
                cancel_event.set()
            raise
        finally:
            if cancel_event is not None:
                cancel_event.set()
            await producer_task

    async def _run_stream_session(
        self,
        request: ChatAskRequest,
        cancel_event: asyncio.Event | None = None,
        emit: Callable[[StreamEvent], None] | None = None,
    ) -> None:
        """Blocking orchestration session executed in a worker thread."""
        self._logger.info(
            "chat.stream.started",
            extra={
                "workspace_id": request.workspace_id,
                "conversation_id": request.conversation_id,
                "llm_connector_name": request.llm_connector_name,
                "metadata_connector_name": request.metadata_connector_name,
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
            metadata_connector_name=request.metadata_connector_name,
            database_connector_name=request.database_connector_name,
            run_sql=request.run_sql,
            question=request.question,
        )
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
        return await self._legacy_runtime_compatibility_adapter.execute_runtime(
            orchestrator=self,
            runtime=runtime,
            cancel_event=cancel_event,
            emit=emit,
            modern_runtime=lambda: self._run_prepared_tool_loop_and_collect(
                runtime=runtime,
                cancel_event=cancel_event,
                on_event=emit,
            ),
        )

    def _run_tool_loop_and_collect_sync(
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
    ) -> ToolLoopResult:
        return self._streaming_legacy_runtime_service.collect_sync(
            session=session,
            llm_connection=llm_connection,
            request=request,
            db_connectors=db_connectors,
            metadata_configured=metadata_configured,
            metadata_connector_names=metadata_connector_names,
            rag_configured=rag_configured,
            confluence_configured=confluence_configured,
            confluence_connector_names=confluence_connector_names,
            jira_configured=jira_configured,
            jira_connector_names=jira_connector_names,
            cancel_event=cancel_event,
            on_event=on_event,
        )

    def _run_prepared_tool_loop_and_collect(
        self,
        *,
        runtime: PreparedRuntimeTurnInput[StreamingToolState],
        cancel_event: asyncio.Event | None,
        on_event: Callable[[StreamEvent], None] | None,
    ) -> ToolLoopResult:
        return self._streaming_runtime_loop_service.run_prepared(
            runtime=runtime,
            cancel_event=cancel_event,
            on_event=on_event,
        )

    async def _run_tool_loop_and_collect(
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
    ) -> ToolLoopResult:
        return self._run_tool_loop_and_collect_sync(
            session=session,
            llm_connection=llm_connection,
            request=request,
            db_connectors=db_connectors,
            metadata_configured=metadata_configured,
            metadata_connector_names=metadata_connector_names,
            rag_configured=rag_configured,
            confluence_configured=confluence_configured,
            confluence_connector_names=confluence_connector_names,
            jira_configured=jira_configured,
            jira_connector_names=jira_connector_names,
            cancel_event=cancel_event,
            on_event=on_event,
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

    def _process_content_for_thinking(
        self, content: str
    ) -> list[ChunkEvent | ThinkingEvent]:
        return self._streaming_content_service.process_content_for_thinking(content)

    def _thinking_events_from_content(self, content: str) -> list[ThinkingEvent]:
        return self._streaming_content_service.thinking_events_from_content(content)

    def _visible_chunk_event(self, content: str) -> ChunkEvent | None:
        return self._streaming_content_service.visible_chunk_event(content)

    def _process_final_content(
        self, content: str
    ) -> list[ChunkEvent | ThinkingEvent]:
        return self._streaming_content_service.process_final_content(content)

    def _deterministic_finalizer_fallback(
        self,
        *,
        state: StreamingToolState,
        raw_final_text: str | None = None,
        uncertainty_flags: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._finalization_policy().deterministic_finalizer_fallback(
            state=state,
            raw_final_text=raw_final_text,
            uncertainty_flags=uncertainty_flags,
        )

    @classmethod
    def _tool_error_stop_payload(
        cls,
        *,
        tool_result: dict[str, Any],
        state: StreamingToolState,
    ) -> dict[str, Any] | None:
        decision = cls._build_tool_execution_policy().resolve_tool_result(
            tool_result=tool_result,
            state=state,
        )
        if isinstance(decision, StopToolResultDecision):
            return decision.payload
        return None

    @staticmethod
    def _incomplete_tool_orchestration_payload(
        state: StreamingToolState,
        answer: str | None = None,
    ) -> dict[str, Any]:
        return StreamingChatOrchestrator._build_tool_execution_policy().build_incomplete_payload(
            state, answer=answer
        )

    @staticmethod
    def _record_duplicate_tool_error(
        *,
        state: StreamingToolState,
        tool_result: dict[str, Any],
    ) -> None:
        ToolExecutionPolicy.record_duplicate_tool_error(
            state=state,
            tool_result=tool_result,
        )

    @staticmethod
    def _summarize_tool_result(result: dict[str, Any]) -> str:
        return StreamingResponseContentService.summarize_tool_result(result)

    @staticmethod
    def _parse_json_payload(content: str) -> tuple[dict[str, Any], bool]:
        return StreamingResponseContentService.parse_json_payload(content)

    def _validate_or_repair_final_payload(
        self,
        *,
        llm_client: LlmClient,
        raw_content: str,
        state: StreamingToolState,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        return self._finalization_policy().validate_or_repair_final_payload(
            llm_client=llm_client,
            raw_content=raw_content,
            state=state,
            response_schema=response_schema,
        )

    @staticmethod
    def _extract_thinking_blocks(content: str) -> tuple[list[str], str]:
        return StreamingResponseContentService.extract_thinking_blocks(content)

    @staticmethod
    def _strip_json_markdown_fence(content: str) -> str:
        return StreamingResponseContentService.strip_json_markdown_fence(content)

    _is_duplicate_tool_call = staticmethod(ToolExecutionPolicy.is_duplicate_tool_call)


_DEFAULT_STREAM_RUN_TOOL_LOOP_AND_COLLECT = (
    StreamingChatOrchestrator._run_tool_loop_and_collect
)
