"""Chat orchestrator - coordinates LLM, metadata, SQL, and conversation services."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

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
from rhetorica_backend.domain.chat.orchestration.chat_runtime_orchestrator import (
    ChatRuntimeOrchestrator,
)
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    RuntimeExecutionResult,
)
from rhetorica_backend.domain.chat.orchestration.orchestration_service_bundles import (
    build_sync_orchestration_service_bundle,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.tool_executor import ToolExecutor
from rhetorica_backend.domain.chat.orchestration.tool_file_capability_service import (
    ToolFileCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.transport_payload_postprocessing import (
    SqlAnswerPostprocessingService,
)
from rhetorica_backend.domain.chat.orchestration.transport_runtime_services import (
    SyncToolLoopRuntimeService,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.documentation import DocumentationRetrievalService
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.domain.results.visualization_planner import (
    VisualizationPlanner,
)
from rhetorica_backend.infra.logging import emit_stdout_event
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
        self._final_payload_builder = FinalPayloadBuilderService(
            normalize_answer=lambda *, answer, state: answer,
        )
        self._payload_postprocessor = SqlAnswerPostprocessingService(sql_router=self._sql_router)
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
        bundle = build_sync_orchestration_service_bundle(
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
            postprocess_llm_payload=self._payload_postprocessor.postprocess,
        )
        self._tool_execution_policy_service = bundle.tool_execution_policy
        self._session_turn_flow_service = bundle.session_turn_flow_service
        self._transport_finalization_service = bundle.transport_finalization_service
        self._transport_finalization_input_factory = bundle.transport_finalization_input_factory
        self._execution_core = bundle.execution_core
        self._sync_tool_loop_runtime_service = SyncToolLoopRuntimeService(
            logger=self._logger,
            execution_core=self._execution_core,
        )

    @staticmethod
    def _emit_diagnostic_event(event: str, **fields: Any) -> None:
        try:
            emit_stdout_event(event, **fields)
        except Exception:
            logger.exception("Failed to emit diagnostic stdout event")

    def _create_llm_client(self, connection: LlmConnectionConfig) -> LlmClient:
        """Create LLM client from connection."""
        return LlmClient(connection)

    def ask(self, request: ChatAskRequest) -> ChatAskResponse:
        """Run the async SDK orchestration from synchronous integrations."""
        return asyncio.run(self.ask_async(request))

    async def ask_async(self, request: ChatAskRequest) -> ChatAskResponse:
        """
        Process chat request.

        Args:
            request: Chat request.

        Returns:
            Chat response.
        """
        if request.coding_mode or request.coding_resources is not None:
            from rhetorica_backend.domain.coding.errors import CodingRunError
            raise CodingRunError("channel_not_allowed", status_code=403)
        self._logger.info(
            "chat.ask.started",
            extra={
                "workspace_id": request.workspace_id,
                "conversation_id": request.conversation_id,
                "llm_connector_name": request.llm_connector_name,
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
            database_connector_name=request.database_connector_name,
            run_sql=request.run_sql,
            question=request.question,
        )

        async def execute_runtime(runtime: Any) -> RuntimeExecutionResult:
            return RuntimeExecutionResult(
                llm_payload=await self._run_tool_loop(
                    llm_client=runtime.llm_client,
                    request=runtime.request,
                    db_connectors=runtime.db_connectors,
                    messages=runtime.messages,
                    tools=runtime.tools,
                    state=runtime.state,
                    tool_executor=runtime.tool_executor,
                    agent_session=runtime.agent_session,
                )
            )

        try:
            result = await self._execution_core.execute_session_async(
                request=request,
                create_conversation_without_llm=True,
                state_factory=ToolOrchestrationState,
                execute_runtime=execute_runtime,
            )
        finally:
            if self._file_capability_service is not None:
                await asyncio.to_thread(self._file_capability_service.close_sessions)
        response = result.response
        self._logger.info(
            "chat.ask.completed",
            extra=result.diagnostics.to_log_fields(workspace_id=request.workspace_id),
        )
        self._emit_diagnostic_event(
            "chat.ask.completed",
            **result.diagnostics.to_log_fields(workspace_id=request.workspace_id),
        )
        return cast(ChatAskResponse, response)

    async def _run_tool_loop(
        self,
        llm_client: LlmClient,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        state: ToolOrchestrationState,
        tool_executor: ToolExecutor,
        agent_session: Any | None = None,
    ) -> dict[str, Any]:
        return await self._sync_tool_loop_runtime_service.run(
            llm_client=llm_client,
            request=request,
            db_connectors=db_connectors,
            messages=messages,
            tools=tools,
            state=state,
            tool_executor=tool_executor,
            agent_session=agent_session,
        )
