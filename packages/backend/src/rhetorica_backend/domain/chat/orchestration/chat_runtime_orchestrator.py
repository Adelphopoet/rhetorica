"""Shared runtime composition for sync and streaming chat adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.orchestration.response_assembly import (
    ChatHistoryPersistenceService,
    ChatResponseAssembler,
)
from rhetorica_backend.domain.chat.orchestration.session import (
    OrchestrationSessionFactory,
)
from rhetorica_backend.domain.chat.orchestration.transport_finalization import (
    HistoryPersistencePolicy,
    SessionFlowTransportFinalizationInputFactory,
)
from rhetorica_backend.domain.chat.orchestration.turn_builder import TurnBuilder
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.documentation import DocumentationRetrievalService
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.domain.results.analysis_document_builder import (
    AnalysisDocumentBuilder,
)
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


@dataclass(slots=True)
class ChatRuntimeOrchestrator:
    """Shared runtime dependencies composed once for both transport adapters."""

    visualization_planner: VisualizationPlanner
    analysis_document_builder: AnalysisDocumentBuilder
    turn_builder: TurnBuilder
    session_factory: OrchestrationSessionFactory
    response_assembler: ChatResponseAssembler
    history_persistence_service: ChatHistoryPersistenceService
    history_persistence_policy: HistoryPersistencePolicy
    transport_finalization_input_factory: SessionFlowTransportFinalizationInputFactory

    @classmethod
    def build(
        cls,
        *,
        llm_connector_repository: LlmConnectorRepository,
        sql_router: SqlRouter,
        conversation_manager: ConversationManager,
        db_connector_repository: Any,
        tool_executor_factory,
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
        file_tools_enabled: bool = False,
        agent_session_factory: Callable[[str], Any] | None = None,
        llm_credential_binding_repository: LlmCredentialBindingRepository | None = None,
    ) -> ChatRuntimeOrchestrator:
        resolved_visualization_planner = visualization_planner or VisualizationPlanner()
        analysis_document_builder = AnalysisDocumentBuilder(resolved_visualization_planner)
        turn_builder = TurnBuilder(
            sql_router=sql_router,
            rag_retriever=rag_retriever,
            documentation_connector_repository=documentation_connector_repository,
            documentation_retrieval_service=documentation_retrieval_service,
            gitlab_connector_repository=gitlab_connector_repository,
            airflow_connector_repository=airflow_connector_repository,
            kubernetes_connector_repository=kubernetes_connector_repository,
            victoriametrics_connector_repository=victoriametrics_connector_repository,
            mcp_connector_repository=mcp_connector_repository,
            workspace_skill_repository=workspace_skill_repository,
            tool_artifact_store=conversation_manager,
            tool_evidence_store=conversation_manager,
            tool_executor_factory=tool_executor_factory,
            file_tools_enabled=file_tools_enabled,
        )
        session_factory = OrchestrationSessionFactory(
            llm_connector_repository=llm_connector_repository,
            conversation_manager=conversation_manager,
            db_connector_repository=db_connector_repository,
            turn_builder=turn_builder,
            sql_router=sql_router,
            capability_registry=capability_registry,
            agent_session_factory=agent_session_factory,
            llm_credential_binding_repository=llm_credential_binding_repository,
        )
        response_assembler = ChatResponseAssembler(
            conversation_manager=conversation_manager,
            visualization_planner=resolved_visualization_planner,
            analysis_document_builder=analysis_document_builder,
        )
        history_persistence_service = ChatHistoryPersistenceService(
            conversation_manager=conversation_manager
        )
        history_persistence_policy = HistoryPersistencePolicy()
        return cls(
            visualization_planner=resolved_visualization_planner,
            analysis_document_builder=analysis_document_builder,
            turn_builder=turn_builder,
            session_factory=session_factory,
            response_assembler=response_assembler,
            history_persistence_service=history_persistence_service,
            history_persistence_policy=history_persistence_policy,
            transport_finalization_input_factory=SessionFlowTransportFinalizationInputFactory(),
        )
