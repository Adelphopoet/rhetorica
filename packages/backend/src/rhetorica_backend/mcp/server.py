"""MCP Server for Rhetorica - provides AI agent access to analytics workspace."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.execution import ExecuteSqlRequest
from rhetorica_backend.domain.chat.metadata import MetadataCatalogService
from rhetorica_backend.domain.chat.orchestration.factory import ChatServiceFactory
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.execution.policy import sql_guard_policy_for_connection
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.openmetadata_connector_repository import (
    OpenMetadataConnectorRepository,
)
from rhetorica_backend.sql_guard.models import DEFAULT_SQL_TIMEOUT_MS, SqlGuardPolicy

logger = logging.getLogger(__name__)


class MCPServer:
    """MCP server wrapper for Rhetorica backend services.

    Provides tools for AI agents to:
    - Execute SQL queries with governance
    - Search metadata and table schemas
    - Chat with analytics context
    - List available connectors and resources
    """

    def __init__(
        self,
        chat_factory: ChatServiceFactory,
        metadata_catalog_service: MetadataCatalogService,
        execution_service: ExecutionService,
        db_connector_repository: ConnectorRepository,
        llm_connector_repository: LlmConnectorRepository,
        openmetadata_connector_repository: OpenMetadataConnectorRepository,
        chat_repository: ChatRepository,
        name: str = "Rhetorica",
    ):
        self._chat_factory = chat_factory
        self._metadata_catalog_service = metadata_catalog_service
        self._execution_service = execution_service
        self._db_connectors = db_connector_repository
        self._llm_connectors = llm_connector_repository
        self._metadata_connectors = openmetadata_connector_repository
        self._chat_repository = chat_repository

        self._mcp = FastMCP(name=name)
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all MCP tools."""

        @self._mcp.tool()
        def run_sql(
            query: str,
            workspace_id: str = "default",
            connector_name: str | None = None,
        ) -> dict[str, Any]:
            """Execute a SQL query with governance and safety checks.

            Args:
                query: SQL query to execute
                workspace_id: Workspace identifier for isolation
                connector_name: Optional database connector name to use

            Returns:
                Execution result with data, columns, and metadata
            """
            return self.run_sql_tool(
                query=query,
                workspace_id=workspace_id,
                connector_name=connector_name,
            )

        @self._mcp.tool()
        def search_metadata(
            search_query: str,
            workspace_id: str = "default",
            metadata_connector_name: str | None = None,
        ) -> list[dict[str, Any]]:
            """Search for tables and columns in metadata registry.

            Args:
                search_query: Search term (table name, column name, description)
                workspace_id: Workspace identifier
                metadata_connector_name: Optional specific metadata connector

            Returns:
                List of matching tables with FQN, columns, and descriptions
            """
            return self.search_metadata_tool(
                search_query=search_query,
                workspace_id=workspace_id,
                metadata_connector_name=metadata_connector_name,
            )

        @self._mcp.tool()
        def ask_chat(
            question: str,
            workspace_id: str = "default",
            conversation_id: str | None = None,
            llm_connector_name: str | None = None,
            metadata_connector_name: str | None = None,
            database_connector_name: str | None = None,
            run_sql: bool = True,
        ) -> dict[str, Any]:
            """Ask a natural language question about analytics data.

            Args:
                question: Natural language question
                workspace_id: Workspace identifier
                conversation_id: Optional conversation ID for multi-turn dialog
                llm_connector_name: LLM connector to use
                metadata_connector_name: Metadata connector for context
                database_connector_name: Database connector for SQL execution
                run_sql: Whether to execute generated SQL

            Returns:
                Answer with optional SQL, execution results, and context
            """
            return self.ask_chat_tool(
                question=question,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                llm_connector_name=llm_connector_name,
                metadata_connector_name=metadata_connector_name,
                database_connector_name=database_connector_name,
                run_sql=run_sql,
            )

        @self._mcp.tool()
        def list_connectors(
            workspace_id: str = "default",
            connector_type: str | None = None,
        ) -> list[dict[str, Any]]:
            """List available connectors for the workspace.

            Args:
                workspace_id: Workspace identifier
                connector_type: Filter by type: 'database', 'llm', 'metadata'

            Returns:
                List of configured connectors with name, type, and status
            """
            return self.list_connectors_tool(
                workspace_id=workspace_id,
                connector_type=connector_type,
            )

        @self._mcp.tool()
        def get_conversation(
            conversation_id: str,
            workspace_id: str = "default",
        ) -> dict[str, Any]:
            """Get conversation history by ID.

            Args:
                conversation_id: Conversation identifier
                workspace_id: Workspace identifier

            Returns:
                Conversation with messages and metadata
            """
            return self.get_conversation_tool(
                conversation_id=conversation_id,
                workspace_id=workspace_id,
            )

        @self._mcp.tool()
        def list_conversations(
            workspace_id: str = "default",
            limit: int = 50,
        ) -> list[dict[str, Any]]:
            """List conversations for a workspace.

            Args:
                workspace_id: Workspace identifier
                limit: Maximum number of conversations to return

            Returns:
                List of conversation summaries
            """
            return self.list_conversations_tool(
                workspace_id=workspace_id,
                limit=limit,
            )

    def _resolve_database_connection(
        self,
        *,
        workspace_id: str,
        connector_name: str | None = None,
    ):
        """Resolve database connection config for execution.

        Args:
            workspace_id: Workspace identifier
            connector_name: Optional specific connector name; uses first available if None

        Returns:
            EngineConnectionConfig or None if no connector found
        """
        if connector_name:
            # Get specific connector
            return self._db_connectors.get_connection_for_execution(
                workspace_id=workspace_id,
                name=connector_name,
            )

        # List connectors and use first available
        connectors = self._db_connectors.list(workspace_id=workspace_id, limit=100)
        if not connectors:
            return None

        # Use first connector with valid connection
        for conn in connectors:
            connection = self._db_connectors.get_connection_for_execution(
                workspace_id=workspace_id,
                name=conn.name,
            )
            if connection is not None:
                return connection

        return None

    def run_sql_tool(
        self,
        *,
        query: str,
        workspace_id: str = "default",
        connector_name: str | None = None,
    ) -> dict[str, Any]:
        """Execute SQL for MCP consumers.

        Args:
            query: SQL query to execute
            workspace_id: Workspace identifier for isolation
            connector_name: Optional database connector name; uses first available if not specified

        Returns:
            Execution result with data, columns, and metadata
        """
        try:
            # Resolve connector for execution
            connection = self._resolve_database_connection(
                workspace_id=workspace_id,
                connector_name=connector_name,
            )
            if connection is None:
                return {
                    "success": False,
                    "error": "No database connector configured for this workspace. "
                    "Please configure a database connector first.",
                }

            request = ExecuteSqlRequest(
                sql=query,
                connection=connection,
                policy=sql_guard_policy_for_connection(
                    connection,
                    SqlGuardPolicy(timeout_ms=DEFAULT_SQL_TIMEOUT_MS),
                ),
                workspace_id=workspace_id,
            )
            result = self._execution_service.execute(request)
        except Exception as exc:
            logger.exception("SQL execution failed")
            return {
                "success": False,
                "error": str(exc),
            }
        return {
            "success": True,
            "data": list(result.preview.rows) if result.preview.rows else [],
            "columns": list(result.preview.columns) if result.preview.columns else [],
            "row_count": result.row_count,
            "execution_id": result.execution_id,
            "status": result.status,
            "warnings": result.warnings,
            "violations": [
                {"code": v.code, "message": v.message}
                for v in result.violations
            ],
        }

    def search_metadata_tool(
        self,
        *,
        search_query: str,
        workspace_id: str = "default",
        metadata_connector_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search metadata without touching deprecated chat services."""
        try:
            connection = self._metadata_catalog_service.resolve_connection(
                workspace_id=workspace_id,
                metadata_connector_name=metadata_connector_name,
            )
            if connection is None:
                return [{"error": "No metadata connector configured"}]
            return self._metadata_catalog_service.search_tables(
                search_query=search_query,
                workspace_id=workspace_id,
                metadata_connector_name=metadata_connector_name,
            )
        except Exception as exc:
            logger.exception("Metadata search failed")
            return [{"error": str(exc)}]

    def ask_chat_tool(
        self,
        *,
        question: str,
        workspace_id: str = "default",
        conversation_id: str | None = None,
        llm_connector_name: str | None = None,
        metadata_connector_name: str | None = None,
        database_connector_name: str | None = None,
        run_sql: bool = True,
        current_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Ask analytics chat through the orchestrator runtime."""
        try:
            request = ChatAskRequest(
                question=question,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                llm_connector_name=llm_connector_name,
                metadata_connector_name=metadata_connector_name,
                database_connector_name=database_connector_name,
                run_sql=run_sql,
            )
            response = self._chat_factory.create_orchestrator(
                current_user_id=current_user_id
            ).ask(request)
        except Exception as exc:
            logger.exception("Chat request failed")
            return {
                "answer": f"Error: {exc}",
                "warnings": ["chat_request_failed"],
            }
        return {
            "answer": response.answer,
            "conversation_id": response.conversation_id,
            "generated_sql": response.generated_sql,
            "selected_database_connector": response.selected_database_connector,
            "assistant_message_id": response.assistant_message_id,
            "metadata_sources": response.metadata_sources,
            "context_tables": response.context_tables,
            "execution": {
                "data": (
                    list(response.execution.preview.rows)
                    if response.execution and response.execution.preview.rows
                    else None
                ),
                "columns": (
                    list(response.execution.preview.columns)
                    if response.execution and response.execution.preview.columns
                    else None
                ),
                "row_count": response.execution.row_count if response.execution else None,
                "status": response.execution.status if response.execution else None,
                "execution_id": response.execution.execution_id if response.execution else None,
            }
            if response.execution
            else None,
            "warnings": response.warnings,
        }

    def list_connectors_tool(
        self,
        *,
        workspace_id: str = "default",
        connector_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List configured connectors for MCP consumers."""
        try:
            results = []
            if connector_type is None or connector_type == "database":
                db_connectors = self._db_connectors.list(
                    workspace_id=workspace_id,
                    limit=500,
                )
                for conn in db_connectors:
                    results.append(
                        {
                            "name": conn.name,
                            "type": "database",
                            "connector_type": conn.connection.engine,
                        }
                    )
            if connector_type is None or connector_type == "llm":
                llm_connectors = self._llm_connectors.list(
                    workspace_id=workspace_id,
                    limit=500,
                )
                for conn in llm_connectors:
                    results.append(
                        {
                            "name": conn.name,
                            "type": "llm",
                            "endpoint_url": conn.connection.base_url,
                            "model": conn.connection.model,
                        }
                    )
            if connector_type is None or connector_type == "metadata":
                metadata_connectors = self._metadata_connectors.list(
                    workspace_id=workspace_id,
                    limit=500,
                )
                for conn in metadata_connectors:
                    results.append(
                        {
                            "name": conn.name,
                            "type": "metadata",
                            "endpoint_url": conn.connection.base_url,
                        }
                    )
        except Exception as exc:
            logger.exception("List connectors failed")
            return [{"error": str(exc)}]
        return results

    def get_conversation_tool(
        self,
        *,
        conversation_id: str,
        workspace_id: str = "default",
        current_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get conversation history by ID."""
        try:
            conversation = self._chat_repository.get_conversation_with_messages(
                conversation_id=conversation_id,
                message_limit=50,
                owner_user_id=current_user_id,
            )
            if not conversation:
                return {"error": "Conversation not found"}
            if conversation.workspace_id != workspace_id:
                return {"error": "Conversation does not belong to this workspace"}
            return {
                "id": conversation.id,
                "workspace_id": conversation.workspace_id,
                "title": conversation.title,
                "created_at": conversation.created_at.isoformat(),
                "updated_at": conversation.updated_at.isoformat(),
                "messages": [
                    {
                        "id": msg.id,
                        "role": msg.role,
                        "content": msg.content,
                        "created_at": msg.created_at.isoformat(),
                        "generated_sql": msg.generated_sql,
                        "context_tables": msg.context_tables,
                        "metadata_sources": msg.metadata_sources,
                    }
                    for msg in conversation.messages
                ],
            }
        except Exception as exc:
            logger.exception("Get conversation failed")
            return {"error": str(exc)}

    def list_conversations_tool(
        self,
        *,
        workspace_id: str = "default",
        limit: int = 50,
        current_user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List conversations for a workspace."""
        try:
            conversations = self._chat_repository.list_conversations(
                workspace_id=workspace_id,
                limit=limit,
                owner_user_id=current_user_id,
            )
        except Exception as exc:
            logger.exception("List conversations failed")
            return [{"error": str(exc)}]
        return [
            {
                "id": conv.id,
                "workspace_id": conv.workspace_id,
                "title": conv.title,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat(),
                "message_count": len(conv.messages) if hasattr(conv, "messages") else 0,
            }
            for conv in conversations
        ]

    @property
    def mcp(self) -> FastMCP:
        """Get underlying FastMCP instance."""
        return self._mcp

    def run(self, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
        """Run the MCP server.

        Args:
            transport: Transport type - 'stdio' for local, 'streamable-http' for network
            host: Host to bind for HTTP transport
            port: Port to bind for HTTP transport
        """
        if transport == "streamable-http":
            self._mcp.run(
                transport=transport,
                host=host,
                port=port,
            )
        else:
            self._mcp.run(transport=transport)
