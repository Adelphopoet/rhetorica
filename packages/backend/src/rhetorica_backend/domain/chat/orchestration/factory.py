"""Factory for creating ChatOrchestrator with dependencies."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.orchestration.chat_orchestrator import (
    ChatOrchestrator,
)
from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
    StreamingChatOrchestrator,
)
from rhetorica_backend.domain.chat.orchestration.tool_file_capability_service import (
    ToolFileCapabilityService,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.documentation import DocumentationRetrievalService
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.infra.postgres.agent_session import PostgresAgentSessionStore
from rhetorica_backend.infra.postgres.airflow_connector_repository import (
    AirflowConnectorRepository,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.documentation_connector_repository import (
    DocumentationConnectorRepository,
)
from rhetorica_backend.infra.postgres.file_artifact_repository import FileArtifactRepository
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
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.victoriametrics_connector_repository import (
    VictoriaMetricsConnectorRepository,
)
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
        rag_connector_repository: RagConnectorRepository,
        documentation_connector_repository: DocumentationConnectorRepository | None = None,
        gitlab_connector_repository: GitLabConnectorRepository | None = None,
        airflow_connector_repository: AirflowConnectorRepository | None = None,
        kubernetes_connector_repository: KubernetesConnectorRepository | None = None,
        victoriametrics_connector_repository: VictoriaMetricsConnectorRepository | None = None,
        mcp_connector_repository: McpConnectorRepository | None = None,
        workspace_skill_repository: WorkspaceSkillRepository | None = None,
        chat_repository: ChatRepository | None = None,
        db: Any | None = None,
        capability_registry: LlmCapabilityRegistry | None = None,
        file_capability_service_factory: Callable[[str], ToolFileCapabilityService] | None = None,
    ):
        self._execution_service = execution_service
        self._db_connector_repository = db_connector_repository
        self._llm_connector_repository = llm_connector_repository
        self._rag_connector_repository = rag_connector_repository
        self._documentation_connector_repository = documentation_connector_repository
        self._gitlab_connector_repository = gitlab_connector_repository
        self._airflow_connector_repository = airflow_connector_repository
        self._kubernetes_connector_repository = kubernetes_connector_repository
        self._victoriametrics_connector_repository = victoriametrics_connector_repository
        self._mcp_connector_repository = mcp_connector_repository
        self._workspace_skill_repository = workspace_skill_repository
        self._chat_repository = chat_repository
        self._db = db
        self._capability_registry = capability_registry
        self._file_capability_service_factory = file_capability_service_factory
        self._logger = logging.getLogger(__name__)

    def create_orchestrator(self, current_user_id: str | None = None) -> ChatOrchestrator:
        """
        Create ChatOrchestrator with all dependencies.

        Returns:
            Configured ChatOrchestrator instance.
        """
        # Create SQL router
        sql_router = SqlRouter(
            execution_service=self._execution_service,
            db_connector_repository=self._db_connector_repository,
            actor_id=current_user_id,
        )

        agent_session_store = (
            PostgresAgentSessionStore(self._db, current_user_id) if self._db is not None else None
        )
        # Create conversation manager
        conversation_manager = ConversationManager(
            chat_repository=self._chat_repository,
            current_user_id=current_user_id,
            artifact_repository=(
                FileArtifactRepository(self._db) if self._db is not None else None
            ),
            agent_session_store=agent_session_store,
        )

        # Create orchestrator
        orchestrator = ChatOrchestrator(
            llm_connector_repository=self._llm_connector_repository,
            sql_router=sql_router,
            conversation_manager=conversation_manager,
            db_connector_repository=self._db_connector_repository,
            rag_retriever=RagInsightRetriever(self._rag_connector_repository),
            documentation_connector_repository=self._documentation_connector_repository,
            documentation_retrieval_service=(
                DocumentationRetrievalService(self._documentation_connector_repository)
                if self._documentation_connector_repository is not None
                else None
            ),
            gitlab_connector_repository=self._gitlab_connector_repository,
            airflow_connector_repository=self._airflow_connector_repository,
            kubernetes_connector_repository=self._kubernetes_connector_repository,
            victoriametrics_connector_repository=self._victoriametrics_connector_repository,
            mcp_connector_repository=self._mcp_connector_repository,
            workspace_skill_repository=self._workspace_skill_repository,
            capability_registry=self._capability_registry,
            file_capability_service=(
                self._file_capability_service_factory(current_user_id)
                if self._file_capability_service_factory is not None and current_user_id is not None
                else None
            ),
            agent_session_factory=(
                agent_session_store.for_conversation if agent_session_store is not None else None
            ),
            llm_credential_binding_repository=(
                LlmCredentialBindingRepository(self._db) if self._db is not None else None
            ),
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
        # Create SQL router
        sql_router = SqlRouter(
            execution_service=self._execution_service,
            db_connector_repository=self._db_connector_repository,
            actor_id=current_user_id,
        )

        agent_session_store = (
            PostgresAgentSessionStore(self._db, current_user_id) if self._db is not None else None
        )
        # Create conversation manager
        conversation_manager = ConversationManager(
            chat_repository=self._chat_repository,
            current_user_id=current_user_id,
            artifact_repository=(
                FileArtifactRepository(self._db) if self._db is not None else None
            ),
            agent_session_store=agent_session_store,
        )

        # Create streaming orchestrator
        orchestrator = StreamingChatOrchestrator(
            llm_connector_repository=self._llm_connector_repository,
            sql_router=sql_router,
            conversation_manager=conversation_manager,
            db_connector_repository=self._db_connector_repository,
            rag_retriever=RagInsightRetriever(self._rag_connector_repository),
            documentation_connector_repository=self._documentation_connector_repository,
            documentation_retrieval_service=(
                DocumentationRetrievalService(self._documentation_connector_repository)
                if self._documentation_connector_repository is not None
                else None
            ),
            gitlab_connector_repository=self._gitlab_connector_repository,
            airflow_connector_repository=self._airflow_connector_repository,
            kubernetes_connector_repository=self._kubernetes_connector_repository,
            victoriametrics_connector_repository=self._victoriametrics_connector_repository,
            mcp_connector_repository=self._mcp_connector_repository,
            workspace_skill_repository=self._workspace_skill_repository,
            capability_registry=self._capability_registry,
            file_capability_service=(
                self._file_capability_service_factory(current_user_id)
                if self._file_capability_service_factory is not None and current_user_id is not None
                else None
            ),
            agent_session_factory=(
                agent_session_store.for_conversation if agent_session_store is not None else None
            ),
            llm_credential_binding_repository=(
                LlmCredentialBindingRepository(self._db) if self._db is not None else None
            ),
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
    rag_connector_repository = RagConnectorRepository(db=db_runtime)
    documentation_connector_repository = DocumentationConnectorRepository(db=db_runtime)
    gitlab_connector_repository = GitLabConnectorRepository(db=db_runtime)
    airflow_connector_repository = AirflowConnectorRepository(db=db_runtime)
    kubernetes_connector_repository = KubernetesConnectorRepository(db=db_runtime)
    victoriametrics_connector_repository = VictoriaMetricsConnectorRepository(db=db_runtime)
    mcp_connector_repository = McpConnectorRepository(db=db_runtime)
    workspace_skill_repository = WorkspaceSkillRepository(db=db_runtime)
    chat_repository = ChatRepository(db=db_runtime)

    return ChatServiceFactory(
        execution_service=execution_service,
        db_connector_repository=db_connector_repository,
        llm_connector_repository=llm_connector_repository,
        rag_connector_repository=rag_connector_repository,
        documentation_connector_repository=documentation_connector_repository,
        gitlab_connector_repository=gitlab_connector_repository,
        airflow_connector_repository=airflow_connector_repository,
        kubernetes_connector_repository=kubernetes_connector_repository,
        victoriametrics_connector_repository=victoriametrics_connector_repository,
        mcp_connector_repository=mcp_connector_repository,
        workspace_skill_repository=workspace_skill_repository,
        chat_repository=chat_repository,
        db=db_runtime,
        capability_registry=get_global_capability_registry(),
    )


@lru_cache(maxsize=1)
def get_cached_factory(database_url: str) -> ChatServiceFactory:
    """Get cached ChatServiceFactory instance."""
    return create_chat_service_factory(database_url)
