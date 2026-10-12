"""Factory for MCP server instances backed by the current database runtime."""

from __future__ import annotations

from rhetorica_backend.domain.chat.orchestration.factory import ChatServiceFactory
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.domain.mcp_connectors import McpToolExecutionService
from rhetorica_backend.domain.metadata.table_search_service import TableSearchService
from rhetorica_backend.infra.llm.capability_registry import (
    get_global_capability_registry,
)
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)
from rhetorica_backend.infra.postgres.kubernetes_connector_repository import (
    KubernetesConnectorRepository,
)
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
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
from rhetorica_backend.mcp.server import MCPServer


class McpServerFactory:
    """Cache MCP server instances per database URL."""

    def __init__(self) -> None:
        self._server: MCPServer | None = None
        self._cache_key: tuple[str, tuple[str, ...], tuple[str, ...]] | None = None

    def get_server(
        self,
        *,
        database_url: str,
        allowed_hosts: tuple[str, ...] = (),
        allowed_origins: tuple[str, ...] = (),
    ) -> MCPServer:
        cache_key = (database_url, allowed_hosts, allowed_origins)
        if self._server is not None and self._cache_key == cache_key:
            return self._server

        execution_service, db_runtime = build_execution_service(database_url)
        db_runtime.create_schema()
        chat_factory = ChatServiceFactory(
            execution_service=execution_service,
            db_connector_repository=ConnectorRepository(db=db_runtime),
            llm_connector_repository=LlmConnectorRepository(db=db_runtime),
            rag_connector_repository=RagConnectorRepository(db=db_runtime),
            gitlab_connector_repository=GitLabConnectorRepository(db=db_runtime),
            kubernetes_connector_repository=KubernetesConnectorRepository(db=db_runtime),
            victoriametrics_connector_repository=VictoriaMetricsConnectorRepository(db=db_runtime),
            mcp_connector_repository=McpConnectorRepository(db=db_runtime),
            workspace_skill_repository=WorkspaceSkillRepository(db=db_runtime),
            chat_repository=ChatRepository(db=db_runtime),
            db=db_runtime,
            capability_registry=get_global_capability_registry(),
        )

        db_connector_repository = ConnectorRepository(db=db_runtime)
        mcp_connector_repository = McpConnectorRepository(db=db_runtime)
        mcp_tool_execution_service = McpToolExecutionService(repository=mcp_connector_repository)
        self._server = MCPServer(
            chat_factory=chat_factory,
            execution_service=execution_service,
            db_connector_repository=db_connector_repository,
            llm_connector_repository=LlmConnectorRepository(db=db_runtime),
            chat_repository=ChatRepository(db=db_runtime),
            agent_repository=AgentProfileRepository(db=db_runtime),
            mcp_tool_execution_service=mcp_tool_execution_service,
            mcp_connector_repository=mcp_connector_repository,
            table_search_service=TableSearchService(
                mcp_tool_execution_service=mcp_tool_execution_service,
                mcp_connector_repository=mcp_connector_repository,
                db_connector_repository=db_connector_repository,
                sql_router=SqlRouter(
                    execution_service=execution_service,
                    db_connector_repository=db_connector_repository,
                ),
            ),
            name="Rhetorica",
            allowed_hosts=list(allowed_hosts),
            allowed_origins=list(allowed_origins),
        )
        self._cache_key = cache_key
        return self._server
