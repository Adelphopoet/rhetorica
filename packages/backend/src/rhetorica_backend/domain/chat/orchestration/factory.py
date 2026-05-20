"""Factory for creating ChatOrchestrator with dependencies."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.metadata import (
    MetadataFetcher,
    resolve_metadata_runtime_services,
)
from rhetorica_backend.domain.chat.orchestration.chat_orchestrator import (
    ChatOrchestrator,
)
from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
    StreamingChatOrchestrator,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.confluence_connector_repository import (
    ConfluenceConnectorRepository,
)
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.jira_connector_repository import (
    JiraConnectorRepository,
)
from rhetorica_backend.infra.postgres.llm_connector_repository import (
    LlmConnectorRepository,
)
from rhetorica_backend.infra.postgres.metadata_usage_store import (
    PostgresMetadataUsageStore,
)
from rhetorica_backend.infra.postgres.openmetadata_connector_repository import (
    OpenMetadataConnectorRepository,
)
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.infra.llm.capability_registry import LlmCapabilityRegistry

logger = logging.getLogger(__name__)


class ChatServiceFactory:
    """Factory for creating ChatOrchestrator with all dependencies."""

    def __init__(
        self,
        execution_service: ExecutionService,
        db_connector_repository: ConnectorRepository,
        llm_connector_repository: LlmConnectorRepository,
        openmetadata_connector_repository: OpenMetadataConnectorRepository,
        rag_connector_repository: RagConnectorRepository,
        confluence_connector_repository: ConfluenceConnectorRepository | None = None,
        jira_connector_repository: JiraConnectorRepository | None = None,
        workspace_skill_repository: WorkspaceSkillRepository | None = None,
        chat_repository: ChatRepository | None = None,
        db: Any | None = None,
        capability_registry: LlmCapabilityRegistry | None = None,
    ):
        self._execution_service = execution_service
        self._db_connector_repository = db_connector_repository
        self._llm_connector_repository = llm_connector_repository
        self._openmetadata_connector_repository = openmetadata_connector_repository
        self._rag_connector_repository = rag_connector_repository
        self._confluence_connector_repository = confluence_connector_repository
        self._jira_connector_repository = jira_connector_repository
        self._workspace_skill_repository = workspace_skill_repository
        self._chat_repository = chat_repository
        self._db = db
        self._metadata_usage_store = PostgresMetadataUsageStore(db) if db is not None else None
        self._capability_registry = capability_registry
        self._logger = logging.getLogger(__name__)

    def create_orchestrator(self, current_user_id: str | None = None) -> ChatOrchestrator:
        """
        Create ChatOrchestrator with all dependencies.

        Returns:
            Configured ChatOrchestrator instance.
        """
        metadata_services = resolve_metadata_runtime_services(
            metadata_usage_store=self._metadata_usage_store
        )
        metadata_fetcher = MetadataFetcher(
            payload_mapper=metadata_services.payload_mapper,
            catalog_gateway=metadata_services.catalog_gateway,
            table_loader=metadata_services.table_loader,
            ranking_service=metadata_services.ranking_service,
            semantic_grounding_service=metadata_services.semantic_grounding_service,
            catalog_search_service=metadata_services.catalog_search_service,
        )

        # Create SQL router
        sql_router = SqlRouter(
            execution_service=self._execution_service,
            db_connector_repository=self._db_connector_repository,
        )

        # Create conversation manager
        conversation_manager = ConversationManager(
            chat_repository=self._chat_repository,
            current_user_id=current_user_id,
        )

        # Create orchestrator
        orchestrator = ChatOrchestrator(
            llm_connector_repository=self._llm_connector_repository,
            openmetadata_connector_repository=self._openmetadata_connector_repository,
            metadata_fetcher=metadata_fetcher,
            sql_router=sql_router,
            conversation_manager=conversation_manager,
            db_connector_repository=self._db_connector_repository,
            rag_retriever=RagInsightRetriever(self._rag_connector_repository),
            confluence_connector_repository=self._confluence_connector_repository,
            jira_connector_repository=self._jira_connector_repository,
            workspace_skill_repository=self._workspace_skill_repository,
            metadata_table_loader=metadata_services.table_loader,
            metadata_catalog_search_service=metadata_services.catalog_search_service,
            metadata_ranking_service=metadata_services.ranking_service,
            metadata_semantic_grounding_service=metadata_services.semantic_grounding_service,
            capability_registry=self._capability_registry,
        )

        return orchestrator

    def create_streaming_orchestrator(
        self,
        current_user_id: str | None = None,
    ) -> StreamingChatOrchestrator:
        """
        Create StreamingChatOrchestrator with all dependencies.

        Returns:
            Configured StreamingChatOrchestrator instance.
        """
        metadata_services = resolve_metadata_runtime_services(
            metadata_usage_store=self._metadata_usage_store
        )
        metadata_fetcher = MetadataFetcher(
            payload_mapper=metadata_services.payload_mapper,
            catalog_gateway=metadata_services.catalog_gateway,
            table_loader=metadata_services.table_loader,
            ranking_service=metadata_services.ranking_service,
            semantic_grounding_service=metadata_services.semantic_grounding_service,
            catalog_search_service=metadata_services.catalog_search_service,
        )

        # Create SQL router
        sql_router = SqlRouter(
            execution_service=self._execution_service,
            db_connector_repository=self._db_connector_repository,
        )

        # Create conversation manager
        conversation_manager = ConversationManager(
            chat_repository=self._chat_repository,
            current_user_id=current_user_id,
        )

        # Create streaming orchestrator
        orchestrator = StreamingChatOrchestrator(
            llm_connector_repository=self._llm_connector_repository,
            openmetadata_connector_repository=self._openmetadata_connector_repository,
            metadata_fetcher=metadata_fetcher,
            sql_router=sql_router,
            conversation_manager=conversation_manager,
            db_connector_repository=self._db_connector_repository,
            rag_retriever=RagInsightRetriever(self._rag_connector_repository),
            confluence_connector_repository=self._confluence_connector_repository,
            jira_connector_repository=self._jira_connector_repository,
            workspace_skill_repository=self._workspace_skill_repository,
            metadata_table_loader=metadata_services.table_loader,
            metadata_catalog_search_service=metadata_services.catalog_search_service,
            metadata_ranking_service=metadata_services.ranking_service,
            metadata_semantic_grounding_service=metadata_services.semantic_grounding_service,
            capability_registry=self._capability_registry,
        )

        return orchestrator


def create_chat_service_factory(
    database_url: str,
) -> ChatServiceFactory:
    """
    Create ChatServiceFactory from database URL.

    Args:
        database_url: Database connection URL.

    Returns:
        Configured ChatServiceFactory instance.
    """
    from rhetorica_backend.domain.execution import build_execution_service
    from rhetorica_backend.infra.llm.capability_registry import (
        get_global_capability_registry,
    )

    # Build execution service and database runtime
    execution_service, db_runtime = build_execution_service(database_url)
    db_runtime.create_schema()

    # Create repositories
    db_connector_repository = ConnectorRepository(db=db_runtime)
    llm_connector_repository = LlmConnectorRepository(db=db_runtime)
    openmetadata_connector_repository = OpenMetadataConnectorRepository(db=db_runtime)
    rag_connector_repository = RagConnectorRepository(db=db_runtime)
    confluence_connector_repository = ConfluenceConnectorRepository(db=db_runtime)
    jira_connector_repository = JiraConnectorRepository(db=db_runtime)
    workspace_skill_repository = WorkspaceSkillRepository(db=db_runtime)
    chat_repository = ChatRepository(db=db_runtime)

    return ChatServiceFactory(
        execution_service=execution_service,
        db_connector_repository=db_connector_repository,
        llm_connector_repository=llm_connector_repository,
        openmetadata_connector_repository=openmetadata_connector_repository,
        rag_connector_repository=rag_connector_repository,
        confluence_connector_repository=confluence_connector_repository,
        jira_connector_repository=jira_connector_repository,
        workspace_skill_repository=workspace_skill_repository,
        chat_repository=chat_repository,
        db=db_runtime,
        capability_registry=get_global_capability_registry(),
    )


@lru_cache(maxsize=1)
def get_cached_factory(database_url: str) -> ChatServiceFactory:
    """Get cached ChatServiceFactory instance."""
    return create_chat_service_factory(database_url)
