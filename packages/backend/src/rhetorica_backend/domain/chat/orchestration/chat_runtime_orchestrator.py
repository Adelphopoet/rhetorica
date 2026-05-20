"""Shared runtime composition for sync and streaming chat adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.metadata import (
    MetadataCatalogSearchService,
    MetadataFetcher,
    MetadataRankingService,
    MetadataSemanticGroundingService,
    MetadataTableLoader,
    resolve_metadata_runtime_services,
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
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.domain.results.analysis_document_builder import (
    AnalysisDocumentBuilder,
)
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


@dataclass(slots=True)
class ChatRuntimeOrchestrator:
    """Shared runtime dependencies composed once for both transport adapters."""

    visualization_planner: VisualizationPlanner
    analysis_document_builder: AnalysisDocumentBuilder
    metadata_table_loader: MetadataTableLoader
    metadata_catalog_search_service: MetadataCatalogSearchService
    metadata_ranking_service: MetadataRankingService
    metadata_semantic_grounding_service: MetadataSemanticGroundingService
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
        openmetadata_connector_repository: OpenMetadataConnectorRepository,
        metadata_fetcher: MetadataFetcher,
        sql_router: SqlRouter,
        conversation_manager: ConversationManager,
        db_connector_repository: Any,
        rag_retriever: RagInsightRetriever | None,
        confluence_connector_repository: ConfluenceConnectorRepository | None,
        jira_connector_repository: JiraConnectorRepository | None,
        workspace_skill_repository: WorkspaceSkillRepository | None,
        visualization_planner: VisualizationPlanner | None,
        metadata_table_loader: MetadataTableLoader | None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None,
        metadata_ranking_service: MetadataRankingService | None,
        metadata_semantic_grounding_service: MetadataSemanticGroundingService | None,
        tool_executor_factory,
        capability_registry: LlmCapabilityRegistry | None = None,
    ) -> ChatRuntimeOrchestrator:
        metadata_services = resolve_metadata_runtime_services(
            metadata_fetcher=metadata_fetcher,
            metadata_table_loader=metadata_table_loader,
            metadata_catalog_search_service=metadata_catalog_search_service,
            metadata_ranking_service=metadata_ranking_service,
            metadata_semantic_grounding_service=metadata_semantic_grounding_service,
        )
        resolved_visualization_planner = visualization_planner or VisualizationPlanner()
        analysis_document_builder = AnalysisDocumentBuilder(resolved_visualization_planner)
        turn_builder = TurnBuilder(
            openmetadata_connector_repository=openmetadata_connector_repository,
            metadata_fetcher=None,
            metadata_table_loader=metadata_services.table_loader,
            metadata_catalog_search_service=metadata_services.catalog_search_service,
            metadata_ranking_service=metadata_services.ranking_service,
            metadata_semantic_grounding_service=metadata_services.semantic_grounding_service,
            sql_router=sql_router,
            rag_retriever=rag_retriever,
            confluence_connector_repository=confluence_connector_repository,
            jira_connector_repository=jira_connector_repository,
            workspace_skill_repository=workspace_skill_repository,
            tool_executor_factory=tool_executor_factory,
        )
        session_factory = OrchestrationSessionFactory(
            llm_connector_repository=llm_connector_repository,
            conversation_manager=conversation_manager,
            db_connector_repository=db_connector_repository,
            turn_builder=turn_builder,
            sql_router=sql_router,
            capability_registry=capability_registry,
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
            metadata_table_loader=metadata_services.table_loader,
            metadata_catalog_search_service=metadata_services.catalog_search_service,
            metadata_ranking_service=metadata_services.ranking_service,
            metadata_semantic_grounding_service=metadata_services.semantic_grounding_service,
            turn_builder=turn_builder,
            session_factory=session_factory,
            response_assembler=response_assembler,
            history_persistence_service=history_persistence_service,
            history_persistence_policy=history_persistence_policy,
            transport_finalization_input_factory=SessionFlowTransportFinalizationInputFactory(),
        )
