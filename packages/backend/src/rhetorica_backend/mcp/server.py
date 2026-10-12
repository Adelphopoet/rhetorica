"""MCP Server for Rhetorica - provides AI agent access to analytics workspace."""

from __future__ import annotations

import json
import logging
from functools import partial
from typing import Annotated, Any, TypeVar, cast

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.execution import EngineConnectionConfig, ExecuteSqlRequest
from rhetorica_backend.domain.chat.orchestration.agent_profile_resolver import (
    AgentProfileResolutionError,
    AgentProfileResolver,
)
from rhetorica_backend.domain.chat.orchestration.factory import ChatServiceFactory
from rhetorica_backend.domain.execution.policy import sql_guard_policy_for_connection
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.mcp_connectors import McpToolExecutionService
from rhetorica_backend.domain.metadata.table_search_service import (
    MetadataServiceDiscoveryError,
    TableSearchService,
)
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.chat_repository import (
    ChatRepository,
)
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.mcp.proxy_tools import McpProxyToolSurface
from rhetorica_backend.sql_guard.models import DEFAULT_SQL_TIMEOUT_MS, SqlGuardPolicy

logger = logging.getLogger(__name__)


class McpToolError(BaseModel):
    """Structured MCP tool error safe to expose to model clients."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class McpSqlExecutionResult(BaseModel):
    """Guarded SQL execution payload exposed to external runtimes."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str | None = None
    status: str | None = None
    normalized_sql: str | None = None
    warnings: list[str] = Field(default_factory=list)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    preview: dict[str, Any] = Field(default_factory=dict)
    row_count: int | None = None
    error_message: str | None = None


class McpRunSqlToolResult(McpSqlExecutionResult):
    success: bool = True
    error: McpToolError | None = None


class McpSearchTablesToolResult(BaseModel):
    """Table candidates from the OpenMetadata-first cascade plus the stages it tried."""

    model_config = ConfigDict(extra="forbid")

    success: bool = True
    tables: list[dict[str, Any]] = Field(default_factory=list)
    tried: list[dict[str, Any]] = Field(default_factory=list)
    found_by: str | None = None
    error: McpToolError | None = None


class McpListMetadataServicesToolResult(BaseModel):
    """Database services visible through the workspace OpenMetadata connector."""

    model_config = ConfigDict(extra="forbid")

    success: bool = True
    services: list[dict[str, Any]] = Field(default_factory=list)
    error: McpToolError | None = None


class McpAskChatToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    answer: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    agent: dict[str, Any] | None = None
    generated_sql: str | None = None
    selected_database_connector: str | None = None
    assistant_message_id: str | None = None
    metadata_sources: list[str] = Field(default_factory=list)
    context_tables: list[str] = Field(default_factory=list)
    execution: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    error: McpToolError | None = None


class McpListConnectorsToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    connectors: list[dict[str, Any]] = Field(default_factory=list)
    error: McpToolError | None = None


class McpListAgentsToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    agents: list[dict[str, Any]] = Field(default_factory=list)
    error: McpToolError | None = None


class McpGetConversationToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    conversation: dict[str, Any] | None = None
    error: McpToolError | None = None


class McpListConversationsToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    conversations: list[dict[str, Any]] = Field(default_factory=list)
    error: McpToolError | None = None


McpTypedResult = TypeVar("McpTypedResult", bound=BaseModel)


class MCPServer:
    """MCP server wrapper for Rhetorica backend services.

    Provides tools for AI agents to:
    - Execute SQL queries with governance
    - Chat with analytics context
    - List available connectors and resources
    """

    def __init__(
        self,
        chat_factory: ChatServiceFactory,
        execution_service: ExecutionService,
        db_connector_repository: ConnectorRepository,
        llm_connector_repository: LlmConnectorRepository,
        chat_repository: ChatRepository,
        agent_repository: AgentProfileRepository | None = None,
        mcp_tool_execution_service: McpToolExecutionService | None = None,
        mcp_connector_repository: Any | None = None,
        table_search_service: TableSearchService | None = None,
        name: str = "Rhetorica",
        allowed_hosts: list[str] | None = None,
        allowed_origins: list[str] | None = None,
    ):
        self._chat_factory = chat_factory
        self._execution_service = execution_service
        self._db_connectors = db_connector_repository
        self._llm_connectors = llm_connector_repository
        self._chat_repository = chat_repository
        self._agent_repository = agent_repository
        self._proxy_tools = (
            McpProxyToolSurface(
                execution_service=mcp_tool_execution_service,
                connector_repository=mcp_connector_repository,
            )
            if mcp_tool_execution_service is not None
            else None
        )
        self._table_search = table_search_service

        self._mcp = FastMCP(
            name=name,
            instructions=(
                "Rhetorica provides governed analytics. Prefer read-only discovery tools, "
                "use run_sql only for explicit analytical queries, and preserve returned SQL "
                "and evidence in user-facing answers. To find a table for a question, call "
                "list_metadata_services when the OpenMetadata service is unknown, then call "
                "search_tables with the exact service_name and connector_name when known. "
                "It searches OpenMetadata and runtime catalogs independently, reporting every "
                "stage it tried. Use connector tools such as openmetadata__* for details and "
                "lineage of a candidate, not for the initial discovery."
            ),
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=allowed_hosts
                or ["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver", "api:8000"],
                allowed_origins=allowed_origins
                or ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
            ),
        )
        self._register_tools()
        self._register_resources_and_prompts()
        if self._proxy_tools is not None:
            self._install_proxy_tool_handlers(self._proxy_tools)

    def _install_proxy_tool_handlers(self, proxy: McpProxyToolSurface) -> None:
        """Extend the native tool surface with synced MCP connector tools of the caller's workspace.

        FastMCP registers tools statically at construction time, while the connector catalog
        lives in the database and is workspace-scoped. The low-level handlers are re-registered
        so `tools/list` and `tools/call` consult the catalog per request; native tools keep
        precedence on name collisions.
        """
        native_list_tools = self._mcp.list_tools
        native_call_tool = self._mcp.call_tool

        async def list_tools() -> list[Tool]:
            tools = list(await native_list_tools())
            workspace_id, _ = self._request_identity(self._mcp.get_context())
            native_names = {tool.name for tool in tools}
            proxy_tools = await anyio.to_thread.run_sync(
                partial(proxy.list_tools, workspace_id=workspace_id)
            )
            tools.extend(tool for tool in proxy_tools if tool.name not in native_names)
            return tools

        async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
            native_names = {tool.name for tool in await native_list_tools()}
            if name not in native_names:
                workspace_id, _ = self._request_identity(self._mcp.get_context())
                is_proxy_tool = await anyio.to_thread.run_sync(
                    partial(proxy.has_tool, workspace_id=workspace_id, name=name)
                )
                if is_proxy_tool:
                    return await anyio.to_thread.run_sync(
                        partial(
                            proxy.call_tool,
                            workspace_id=workspace_id,
                            name=name,
                            arguments=arguments,
                        )
                    )
            return await native_call_tool(name, arguments)

        self._mcp._mcp_server.list_tools()(list_tools)
        self._mcp._mcp_server.call_tool(validate_input=False)(call_tool)

    def _register_tools(self) -> None:
        """Register all MCP tools."""

        @self._mcp.tool(
            title="Run governed SQL",
            description=(
                "Execute one read-only SQL query through Rhetorica governance and return a "
                "bounded preview plus normalized SQL, violations, warnings, and execution id."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
            structured_output=True,
        )
        async def run_sql(
            query: Annotated[str, Field(min_length=1, description="Read-only SQL to execute")],
            ctx: Context,
            connector_name: Annotated[
                str | None,
                Field(description="Exact database connector name; omit to use the workspace default"),
            ] = None,
        ) -> McpRunSqlToolResult:
            workspace_id, _ = self._request_identity(ctx)
            payload = await anyio.to_thread.run_sync(
                partial(
                    self.run_sql_tool,
                    query=query,
                    workspace_id=workspace_id,
                    connector_name=connector_name,
                )
            )
            return self._typed_result(payload, McpRunSqlToolResult)

        @self._mcp.tool(
            title="List metadata services",
            description=(
                "List every OpenMetadata database service visible in this workspace. Use an "
                "exact returned name as search_tables.service_name; database connector names "
                "are separate and come from list_connectors."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
            structured_output=True,
        )
        async def list_metadata_services(ctx: Context) -> McpListMetadataServicesToolResult:
            workspace_id, _ = self._request_identity(ctx)
            payload = await anyio.to_thread.run_sync(
                partial(self.list_metadata_services_tool, workspace_id=workspace_id)
            )
            return self._typed_result(payload, McpListMetadataServicesToolResult)

        @self._mcp.tool(
            title="Search tables",
            description=(
                "Find candidate tables for a natural-language question. OpenMetadata-first "
                "cascade: keyword search scoped to an exact OpenMetadata service or workspace "
                "engine types, then unscoped when no service was selected, then by column names, "
                "plus runtime catalog probes. Metadata services and runtime connectors remain "
                "separate. Returns compact candidates and statuses for every stage."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
            structured_output=True,
        )
        async def search_tables(
            query: Annotated[
                str,
                Field(min_length=1, description="Question or keywords, any language"),
            ],
            ctx: Context,
            connector_name: Annotated[
                str | None,
                Field(description="Exact runtime database connector; omit for all"),
            ] = None,
            service_name: Annotated[
                str | None,
                Field(description="Exact OpenMetadata database service; use list_metadata_services"),
            ] = None,
            database: Annotated[
                str | None,
                Field(description="Limit to one database/schema name, e.g. dm"),
            ] = None,
            size: Annotated[int, Field(ge=1, le=50, description="Maximum candidates")] = 10,
        ) -> McpSearchTablesToolResult:
            workspace_id, _ = self._request_identity(ctx)
            payload = await anyio.to_thread.run_sync(
                partial(
                    self.search_tables_tool,
                    query=query,
                    workspace_id=workspace_id,
                    connector_name=connector_name,
                    service_name=service_name,
                    database=database,
                    size=size,
                )
            )
            return self._typed_result(payload, McpSearchTablesToolResult)

        @self._mcp.tool(
            title="Ask Rhetorica analyst",
            description=(
                "Ask an analytics question using governed workspace context. The operation "
                "creates conversation history and may execute read-only SQL."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
            structured_output=True,
        )
        async def ask_chat(
            question: Annotated[str, Field(min_length=1, description="Natural-language analytics question")],
            ctx: Context,
            conversation_id: str | None = None,
            agent_id: str | None = None,
            llm_connector_name: str | None = None,
            database_connector_name: str | None = None,
            run_sql: bool = True,
        ) -> McpAskChatToolResult:
            workspace_id, current_user_id = self._request_identity(ctx)
            payload = await anyio.to_thread.run_sync(
                partial(
                    self.ask_chat_tool,
                    question=question,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    llm_connector_name=llm_connector_name,
                    database_connector_name=database_connector_name,
                    run_sql=run_sql,
                    current_user_id=current_user_id,
                )
            )
            return self._typed_result(payload, McpAskChatToolResult)

        @self._mcp.tool(
            title="List workspace agents",
            description=(
                "List active workspace agents with safe metadata for choosing the profile "
                "used by ask_chat. Admin prompts and connector secrets are never returned."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
            structured_output=True,
        )
        async def list_agents(
            ctx: Context,
            limit: Annotated[int, Field(ge=1, le=100)] = 100,
        ) -> McpListAgentsToolResult:
            workspace_id, current_user_id = self._request_identity(ctx)
            payload = await anyio.to_thread.run_sync(
                partial(
                    self.list_agents_tool,
                    workspace_id=workspace_id,
                    limit=limit,
                    current_user_id=current_user_id,
                )
            )
            return self._typed_result(payload, McpListAgentsToolResult)

        @self._mcp.tool(
            title="List workspace connectors",
            description="List safe metadata for available database and LLM connectors.",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
            structured_output=True,
        )
        async def list_connectors(
            ctx: Context,
            connector_type: str | None = None,
        ) -> McpListConnectorsToolResult:
            workspace_id, _ = self._request_identity(ctx)
            payload = await anyio.to_thread.run_sync(
                partial(
                    self.list_connectors_tool,
                    workspace_id=workspace_id,
                    connector_type=connector_type,
                )
            )
            return self._typed_result(payload, McpListConnectorsToolResult)

        @self._mcp.tool(
            title="Get conversation",
            description="Read one conversation owned by the authenticated user.",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
            structured_output=True,
        )
        async def get_conversation(
            conversation_id: str,
            ctx: Context,
        ) -> McpGetConversationToolResult:
            workspace_id, current_user_id = self._request_identity(ctx)
            payload = await anyio.to_thread.run_sync(
                partial(
                    self.get_conversation_tool,
                    conversation_id=conversation_id,
                    workspace_id=workspace_id,
                    current_user_id=current_user_id,
                )
            )
            return self._typed_result(payload, McpGetConversationToolResult)

        @self._mcp.tool(
            title="List conversations",
            description="List recent conversations owned by the authenticated user.",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
            structured_output=True,
        )
        async def list_conversations(
            ctx: Context,
            limit: int = 50,
        ) -> McpListConversationsToolResult:
            workspace_id, current_user_id = self._request_identity(ctx)
            payload = await anyio.to_thread.run_sync(
                partial(
                    self.list_conversations_tool,
                    workspace_id=workspace_id,
                    limit=limit,
                    current_user_id=current_user_id,
                )
            )
            return self._typed_result(payload, McpListConversationsToolResult)

    def _register_resources_and_prompts(self) -> None:
        @self._mcp.resource(
            "rhetorica://connection-guide",
            name="rhetorica_connection_guide",
            title="Rhetorica MCP connection guide",
            description="Safe connection and tool-selection guidance for MCP clients.",
            mime_type="text/markdown",
        )
        def connection_guide() -> str:
            return (
                "# Rhetorica MCP\n\n"
                "Connect to `/mcp` with `Authorization: Bearer <personal access token>`. "
                "Use `mcp:read` for discovery and history; add `mcp:execute` for governed "
                "SQL, analytics chat, and snapshot persistence. Keep sensitive actions "
                "approval-gated and never place the token in prompts or URLs."
            )

        @self._mcp.prompt(
            name="analyze_data",
            title="Analyze governed data",
            description="Create a transparent analytics request that preserves SQL and evidence.",
        )
        def analyze_data(
            question: str,
            connector_name: str | None = None,
        ) -> str:
            connector = f" Use database connector `{connector_name}`." if connector_name else ""
            return (
                f"Answer this analytics question using Rhetorica: {question}.{connector} "
                "Show normalized SQL, execution warnings, source tables, and evidence. "
                "Ask for clarification instead of guessing missing business definitions."
            )

    @staticmethod
    def _request_identity(ctx: Context) -> tuple[str, str | None]:
        request = ctx.request_context.request
        state = getattr(request, "state", None)
        auth_context = getattr(state, "auth_context", None)
        if auth_context is None:
            return "default", None
        return auth_context.workspace_id, auth_context.user_id

    @staticmethod
    def _typed_result(
        payload: dict[str, Any],
        model: type[McpTypedResult],
    ) -> McpTypedResult:
        if payload.get("success") is False:
            model.model_validate(payload)
            structured = payload
            return cast(
                McpTypedResult,
                CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps(structured, ensure_ascii=False, sort_keys=True),
                        )
                    ],
                    structuredContent=structured,
                    isError=True,
                ),
            )
        return model.model_validate(payload)

    def _resolve_database_connection(
        self,
        *,
        workspace_id: str,
        connector_name: str | None = None,
    ) -> EngineConnectionConfig | None:
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
            Execution result with a bounded preview and audit metadata
        """
        try:
            # Resolve connector for execution
            connection = self._resolve_database_connection(
                workspace_id=workspace_id,
                connector_name=connector_name,
            )
            if connection is None:
                return self._tool_error(
                    code="database_connector_not_configured",
                    message="No database connector configured for this workspace.",
                )

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
        except Exception:
            logger.exception("SQL execution failed")
            return self._tool_error(
                code="sql_execution_failed",
                message="SQL execution failed.",
            )
        payload = McpSqlExecutionResult(
            execution_id=result.execution_id,
            status=result.status,
            normalized_sql=result.normalized_sql or query,
            warnings=list(result.warnings),
            violations=[v.model_dump(mode="json") for v in result.violations],
            preview={
                "columns": list(result.preview.columns),
                "rows": list(result.preview.rows),
            },
            row_count=result.row_count,
            error_message=result.error_message,
        ).model_dump(mode="json")
        return {
            "success": True,
            **payload,
            "error": None,
        }

    def list_metadata_services_tool(
        self,
        *,
        workspace_id: str = "default",
    ) -> dict[str, Any]:
        """List OpenMetadata database services for MCP consumers."""
        if self._table_search is None:
            return self._tool_error(
                code="table_search_not_configured",
                message="Metadata service discovery is not configured for this server.",
            )
        try:
            services = self._table_search.list_metadata_services(workspace_id=workspace_id)
        except MetadataServiceDiscoveryError as exc:
            return self._tool_error(code=exc.code, message=exc.message)
        except Exception:
            logger.exception("Metadata service discovery failed")
            return self._tool_error(
                code="metadata_service_discovery_failed",
                message="Metadata service discovery failed.",
            )
        return {"success": True, "services": services, "error": None}

    def search_tables_tool(
        self,
        *,
        query: str,
        workspace_id: str = "default",
        connector_name: str | None = None,
        service_name: str | None = None,
        database: str | None = None,
        size: int = 10,
    ) -> dict[str, Any]:
        """Run the OpenMetadata-first table search cascade for MCP consumers."""
        if self._table_search is None:
            return self._tool_error(
                code="table_search_not_configured",
                message="Table search is not configured for this server.",
            )
        try:
            result = self._table_search.search(
                workspace_id=workspace_id,
                query=query,
                connector_name=connector_name,
                service_name=service_name,
                database=database,
                size=size,
            )
        except Exception:
            logger.exception("Table search failed")
            return self._tool_error(
                code="table_search_failed",
                message="Table search failed.",
            )
        return {"success": True, **result.to_payload(), "error": None}

    def ask_chat_tool(
        self,
        *,
        question: str,
        workspace_id: str = "default",
        conversation_id: str | None = None,
        agent_id: str | None = None,
        llm_connector_name: str | None = None,
        database_connector_name: str | None = None,
        run_sql: bool = True,
        current_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Ask analytics chat through the orchestrator runtime."""
        existing_conversation = None
        if conversation_id is not None:
            existing_conversation = self._chat_repository.get_conversation(
                conversation_id=conversation_id,
                owner_user_id=current_user_id,
            )
            if (
                existing_conversation is None
                or existing_conversation.workspace_id != workspace_id
            ):
                return self._tool_error(
                    code="conversation_not_found",
                    message="Conversation not found.",
                )
        try:
            request = ChatAskRequest(
                question=question,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                llm_connector_name=llm_connector_name,
                database_connector_name=database_connector_name,
                run_sql=run_sql,
            )
            resolved_agent = None
            if self._agent_repository is not None:
                request, resolved_agent = AgentProfileResolver(self._agent_repository).resolve(
                    request=request,
                    current_user_id=current_user_id,
                    existing_conversation=existing_conversation,
                )
            elif request.agent_id is not None or (
                existing_conversation is not None and existing_conversation.agent_id is not None
            ):
                return self._tool_error(
                    code="agent_runtime_unavailable",
                    message="Agent runtime is not available.",
                )
            response = self._chat_factory.create_orchestrator(current_user_id=current_user_id).ask(
                request
            )
        except AgentProfileResolutionError as exc:
            return self._tool_error(
                code=exc.code,
                message=(
                    "Conversation agent is locked."
                    if exc.code == "conversation_agent_locked"
                    else "Agent not found."
                ),
            )
        except Exception:
            logger.exception("Chat request failed")
            return {
                "success": False,
                "answer": "Chat request failed.",
                "warnings": ["chat_request_failed"],
                "error": McpToolError(
                    code="chat_request_failed",
                    message="Chat request failed.",
                ).model_dump(mode="json"),
            }
        return {
            "success": True,
            "answer": response.answer,
            "conversation_id": response.conversation_id,
            "agent_id": request.agent_id,
            "agent": self._safe_agent_summary(resolved_agent) if resolved_agent else None,
            "generated_sql": response.generated_sql,
            "selected_database_connector": response.selected_database_connector,
            "assistant_message_id": response.assistant_message_id,
            "metadata_sources": response.metadata_sources,
            "context_tables": response.context_tables,
            "execution": McpSqlExecutionResult(
                execution_id=response.execution.execution_id,
                status=response.execution.status,
                normalized_sql=response.execution.normalized_sql,
                warnings=list(response.execution.warnings),
                violations=[
                    violation.model_dump(mode="json")
                    for violation in response.execution.violations
                ],
                preview={
                    "columns": list(response.execution.preview.columns),
                    "rows": list(response.execution.preview.rows),
                },
                row_count=response.execution.row_count,
                error_message=response.execution.error_message,
            ).model_dump(mode="json")
            if response.execution
            else None,
            "warnings": response.warnings,
            "error": None,
        }

    def list_agents_tool(
        self,
        *,
        workspace_id: str = "default",
        limit: int = 100,
        current_user_id: str | None = None,
    ) -> dict[str, Any]:
        """List active agents without exposing their private runtime policy."""
        if self._agent_repository is None:
            return self._tool_error(
                code="agent_runtime_unavailable",
                message="Agent runtime is not available.",
            )
        try:
            agents = self._agent_repository.list(
                workspace_id=workspace_id,
                current_user_id=current_user_id,
                include_inactive=False,
                limit=max(1, min(limit, 100)),
            )
        except Exception:
            logger.exception("Agent listing failed")
            return self._tool_error(
                code="list_agents_failed",
                message="Agent listing failed.",
            )
        return {
            "success": True,
            "agents": [self._safe_agent_summary(agent) for agent in agents],
            "error": None,
        }

    @staticmethod
    def _safe_agent_summary(agent: Any) -> dict[str, Any]:
        return {
            "id": agent.id,
            "slug": agent.slug,
            "title": agent.title,
            "description": agent.description,
            "run_sql_allowed": agent.settings.run_sql_allowed,
            "star_count": agent.star_count,
            "is_starred": agent.is_starred,
            "is_favorite": agent.is_favorite,
        }

    def list_connectors_tool(
        self,
        *,
        workspace_id: str = "default",
        connector_type: str | None = None,
    ) -> dict[str, Any]:
        """List configured connectors for MCP consumers."""
        try:
            results: list[dict[str, Any]] = []
            if connector_type is None or connector_type == "database":
                db_connectors = self._db_connectors.list(
                    workspace_id=workspace_id,
                    limit=500,
                )
                for db_connector in db_connectors:
                    results.append({
                        "name": db_connector.name,
                        "type": "database",
                        "connector_type": db_connector.connection.engine,
                    })
            if connector_type is None or connector_type == "llm":
                llm_connectors = self._llm_connectors.list(
                    workspace_id=workspace_id,
                    limit=500,
                )
                for llm_connector in llm_connectors:
                    results.append({
                        "name": llm_connector.name,
                        "type": "llm",
                        "endpoint_url": llm_connector.connection.base_url,
                        "model": llm_connector.connection.model,
                    })
        except Exception:
            logger.exception("List connectors failed")
            error = self._tool_error(
                code="list_connectors_failed",
                message="Connector listing failed.",
            )
            return {"success": False, "connectors": [], "error": error["error"]}
        return {"success": True, "connectors": results, "error": None}

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
                return self._tool_error(
                    code="conversation_not_found",
                    message="Conversation not found.",
                )
            if conversation.workspace_id != workspace_id:
                return self._tool_error(
                    code="workspace_mismatch",
                    message="Conversation does not belong to this workspace.",
                )
            return {
                "success": True,
                "conversation": {
                    "id": conversation.id,
                    "workspace_id": conversation.workspace_id,
                    "title": conversation.title,
                    "agent_id": conversation.agent_id,
                    "created_at": conversation.created_at.isoformat(),
                    "updated_at": conversation.updated_at.isoformat(),
                    "messages": [
                        {
                            "id": msg.id,
                            "role": msg.role,
                            "content": msg.content,
                            "agent_id": msg.agent_id,
                            "created_at": msg.created_at.isoformat(),
                            "generated_sql": msg.generated_sql,
                            "context_tables": msg.context_tables,
                            "metadata_sources": msg.metadata_sources,
                        }
                        for msg in conversation.messages
                    ],
                },
                "error": None,
            }
        except Exception:
            logger.exception("Get conversation failed")
            return self._tool_error(
                code="get_conversation_failed",
                message="Conversation lookup failed.",
            )

    def list_conversations_tool(
        self,
        *,
        workspace_id: str = "default",
        limit: int = 50,
        current_user_id: str | None = None,
    ) -> dict[str, Any]:
        """List conversations for a workspace."""
        try:
            conversations = self._chat_repository.list_conversations(
                workspace_id=workspace_id,
                limit=limit,
                owner_user_id=current_user_id,
            )
        except Exception:
            logger.exception("List conversations failed")
            error = self._tool_error(
                code="list_conversations_failed",
                message="Conversation listing failed.",
            )
            return {"success": False, "conversations": [], "error": error["error"]}
        return {
            "success": True,
            "conversations": [
                {
                    "id": conv.id,
                    "workspace_id": conv.workspace_id,
                    "title": conv.title,
                    "agent_id": conv.agent_id,
                    "created_at": conv.created_at.isoformat(),
                    "updated_at": conv.updated_at.isoformat(),
                    "message_count": len(conv.messages) if hasattr(conv, "messages") else 0,
                }
                for conv in conversations
            ],
            "error": None,
        }

    @staticmethod
    def _tool_error(
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "error": McpToolError(
                code=code,
                message=message,
                details=details or {},
            ).model_dump(mode="json"),
        }

    @property
    def mcp(self) -> FastMCP:
        """Get underlying FastMCP instance."""
        return self._mcp
