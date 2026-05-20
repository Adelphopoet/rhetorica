"""Chat orchestrator - coordinates LLM, metadata, SQL, and conversation services."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.chat import (
    ChatAskRequest,
    ChatAskResponse,
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
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    WorkspaceCapabilitySurface,
    build_workspace_capability_surface,
)
from rhetorica_backend.domain.chat.orchestration.chat_runtime_orchestrator import (
    ChatRuntimeOrchestrator,
)
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
    RecoveryFinalizationService,
)
from rhetorica_backend.domain.chat.orchestration.legacy_compatibility import (
    LegacySyncOrchestratorCompatibilityAdapter,
)
from rhetorica_backend.domain.chat.orchestration.llm_finalization_policy import (
    LlmFinalizationPolicy,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    RuntimeExecutionResult,
    StopToolResultDecision,
    SyncTransportFinalizationResult,
)
from rhetorica_backend.domain.chat.orchestration.orchestration_service_bundles import (
    build_sync_orchestration_service_bundle,
    build_tool_execution_policy,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_policy import (
    ToolExecutionPolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_executor import ToolExecutor
from rhetorica_backend.domain.chat.orchestration.transport_payload_postprocessing import (
    SqlAnswerPostprocessingService,
)
from rhetorica_backend.domain.chat.orchestration.transport_runtime_services import (
    SyncLegacyOrchestrationService,
    SyncToolLoopRuntimeService,
)
from rhetorica_backend.domain.chat.orchestration.turn_policy import (
    resolve_turn_policy,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.domain.results.visualization_planner import (
    VisualizationPlanner,
)
from rhetorica_backend.infra.logging import emit_stdout_event
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
MAX_PROTOCOL_REPAIR_ATTEMPTS = 8


class ChatOrchestrator:
    """
    Orchestrates chat interactions with LLM.

    Coordinates:
    - LLM client for completions
    - Metadata resolver and fetcher for table discovery
    - SQL router for query execution
    - Conversation manager for history
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
        self._active_orchestration_session = None
        self._final_payload_builder = FinalPayloadBuilderService(
            normalize_answer=lambda *, answer, state: answer,
        )
        self._payload_postprocessor = SqlAnswerPostprocessingService(
            sql_router=self._sql_router
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
        bundle = build_sync_orchestration_service_bundle(
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
            postprocess_llm_payload=self._payload_postprocessor.postprocess,
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
        self._sync_tool_loop_runtime_service = SyncToolLoopRuntimeService(
            logger=self._logger,
            execution_core=self._execution_core,
        )
        self._sync_legacy_orchestration_service = SyncLegacyOrchestrationService(
            logger=self._logger,
            session_factory=self._session_factory,
            turn_builder=self._turn_builder,
            create_llm_client=lambda connection: self._create_llm_client(connection),
            run_tool_loop=self._sync_tool_loop_runtime_service.run,
        )
        self._legacy_compatibility_adapter = self._build_legacy_compatibility_adapter()

    @staticmethod
    def _emit_diagnostic_event(event: str, **fields: Any) -> None:
        try:
            emit_stdout_event(event, **fields)
        except Exception:
            logger.exception("Failed to emit diagnostic stdout event")

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

    def _build_legacy_compatibility_adapter(
        self,
    ) -> LegacySyncOrchestratorCompatibilityAdapter:
        return LegacySyncOrchestratorCompatibilityAdapter(
            session_factory=self._session_factory,
            session_turn_flow_service=self._session_turn_flow_service,
            default_execute_orchestration=_DEFAULT_CHAT_EXECUTE_LLM_ORCHESTRATION,
        )

    def ask(self, request: ChatAskRequest) -> ChatAskResponse:
        """
        Process chat request.

        Args:
            request: Chat request.

        Returns:
            Chat response.
        """
        self._logger.info(
            "chat.ask.started",
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
        self._emit_diagnostic_event(
            "chat.ask.started",
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
            llm_connector_name=request.llm_connector_name,
            metadata_connector_name=request.metadata_connector_name,
            database_connector_name=request.database_connector_name,
            run_sql=request.run_sql,
            question=request.question,
        )

        result = self._legacy_compatibility_adapter.run(
            orchestrator=self,
            request=request,
            modern_flow=lambda: self._execution_core.execute_session(
                request=request,
                create_conversation_without_llm=True,
                state_factory=ToolOrchestrationState,
                execute_runtime=lambda runtime: RuntimeExecutionResult(
                    llm_payload=self._run_tool_loop(
                        llm_client=runtime.llm_client,
                        request=runtime.request,
                        db_connectors=runtime.db_connectors,
                        messages=runtime.messages,
                        tools=runtime.tools,
                        state=runtime.state,
                        tool_executor=runtime.tool_executor,
                        protocol_repair_enabled=runtime.protocol_repair_enabled,
                    )
                ),
            ),
        )
        if not isinstance(result, SyncTransportFinalizationResult):
            result = self._transport_finalization_service.finalize(
                finalization_input=self._transport_finalization_input_factory.build(
                    request=request,
                    flow=result,
                )
            )
        response = result.response
        self._logger.info(
            "chat.ask.completed",
            extra=result.diagnostics.to_log_fields(workspace_id=request.workspace_id),
        )
        self._emit_diagnostic_event(
            "chat.ask.completed",
            **result.diagnostics.to_log_fields(workspace_id=request.workspace_id),
        )
        return response

    def _execute_llm_orchestration(
        self,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        conversation_id: str | None,
        warnings: list[str],
    ) -> dict[str, Any]:
        _ = db_connectors
        return self._sync_legacy_orchestration_service.execute(
            request=request,
            conversation_id=conversation_id,
            warnings=warnings,
            active_session=self._active_orchestration_session,
        )

    def _run_tool_loop(
        self,
        llm_client: LlmClient,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        state: ToolOrchestrationState,
        tool_executor: ToolExecutor,
        protocol_repair_enabled: bool = False,
    ) -> dict[str, Any]:
        return self._sync_tool_loop_runtime_service.run(
            llm_client=llm_client,
            request=request,
            db_connectors=db_connectors,
            messages=messages,
            tools=tools,
            state=state,
            tool_executor=tool_executor,
            protocol_repair_enabled=protocol_repair_enabled,
        )

    def _workspace_capability_surface(
        self,
        *,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
    ) -> WorkspaceCapabilitySurface:
        workspace_snapshot = self._turn_builder.inspect_workspace(
            request=request,
            db_connectors=db_connectors,
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
        return build_workspace_capability_surface(
            has_db_connectors=workspace_snapshot.has_db_connectors,
            metadata_configured=workspace_snapshot.metadata_configured,
            rag_configured=workspace_snapshot.rag_configured,
            confluence_configured=workspace_snapshot.confluence_configured,
            jira_configured=workspace_snapshot.jira_configured,
            policy=policy,
            run_sql_requested=request.run_sql,
        )

    def _build_final_payload_from_parsed(
        self,
        *,
        parsed_payload: dict[str, Any],
        fallback_answer: str | None,
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._final_payload_builder.build_final_payload_from_parsed(
            parsed_payload=parsed_payload,
            fallback_answer=fallback_answer,
            state=state,
        )

    def _postprocess_llm_payload(
        self,
        *,
        request: ChatAskRequest,
        llm_payload: dict[str, Any],
        db_connectors: list[ConnectorItem],
        warnings: list[str],
    ) -> dict[str, Any]:
        postprocessor = SqlAnswerPostprocessingService(sql_router=self._sql_router)
        return postprocessor.postprocess(
            request=request,
            llm_payload=llm_payload,
            db_connectors=db_connectors,
            warnings=warnings,
        )

    def _deterministic_finalizer_fallback(
        self,
        *,
        state: ToolOrchestrationState,
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
        state: ToolOrchestrationState,
    ) -> dict[str, Any] | None:
        decision = cls._build_tool_execution_policy().resolve_tool_result(
            tool_result=tool_result,
            state=state,
        )
        if isinstance(decision, StopToolResultDecision):
            return decision.payload
        return None

    @staticmethod
    def _record_duplicate_tool_error(
        *,
        state: ToolOrchestrationState,
        tool_result: dict[str, Any],
    ) -> None:
        ToolExecutionPolicy.record_duplicate_tool_error(
            state=state,
            tool_result=tool_result,
        )

    @staticmethod
    def _incomplete_tool_orchestration_payload(
        state: ToolOrchestrationState,
        answer: str | None = None,
    ) -> dict[str, Any]:
        return ChatOrchestrator._build_tool_execution_policy().build_incomplete_payload(
            state, answer=answer
        )

    def _validate_or_repair_final_payload(
        self,
        llm_client: LlmClient,
        raw_content: str,
        state: ToolOrchestrationState,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        return self._finalization_policy().validate_or_repair_final_payload(
            llm_client=llm_client,
            raw_content=raw_content,
            state=state,
            response_schema=response_schema,
        )

    _is_duplicate_tool_call = staticmethod(ToolExecutionPolicy.is_duplicate_tool_call)


_DEFAULT_CHAT_EXECUTE_LLM_ORCHESTRATION = ChatOrchestrator._execute_llm_orchestration
