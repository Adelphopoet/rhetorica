"""Tool executor - executes LLM tool calls."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import (
    ChatAskRequest,
    OptimizationTrace,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
)
from rhetorica_backend.domain.chat.jira.service import JiraService
from rhetorica_backend.domain.chat.metadata import (
    MetadataCatalogSearchService,
    MetadataFetcher,
    MetadataRankingService,
    MetadataResolver,
    MetadataSemanticGroundingService,
    MetadataTableLoader,
)
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    register_tool_usage,
)
from rhetorica_backend.domain.chat.orchestration.models import ToolCallRequest
from rhetorica_backend.domain.chat.orchestration.tool_capability import (
    BoundToolCapability,
)
from rhetorica_backend.domain.chat.orchestration.tool_capability_registry import (
    ToolCapabilityRegistry,
)
from rhetorica_backend.domain.chat.orchestration.tool_docs_capability_service import (
    ToolDocsCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_support_kernel import (
    ToolExecutionSupportKernel,
)
from rhetorica_backend.domain.chat.orchestration.tool_jira_capability_service import (
    ToolJiraCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_metadata_capability_service import (
    ToolMetadataCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_profile_capability_service import (
    ToolProfileCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_sql_capability_service import (
    ToolSqlCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_visualization_capability_service import (
    ToolVisualizationCapabilityService,
)
from rhetorica_backend.domain.chat.sql.optimization import SqlOptimizationService
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.infra.postgres.confluence_connector_repository import (
    ConfluenceConnectorRepository,
)
from rhetorica_backend.infra.postgres.jira_connector_repository import (
    JiraConnectorRepository,
)
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import (
        ToolOrchestrationState,
    )


logger = logging.getLogger(__name__)


class ToolExecutor:
    """Thin coordinator that composes capability-family services."""

    def __init__(
        self,
        metadata_resolver: MetadataResolver,
        metadata_fetcher: MetadataFetcher | None,
        sql_router: SqlRouter,
        rag_retriever: RagInsightRetriever | None = None,
        confluence_connector_repository: ConfluenceConnectorRepository | None = None,
        jira_connector_repository: JiraConnectorRepository | None = None,
        semantic_preflight_service: SemanticSqlPreflightService | None = None,
        workspace_skill_repository: WorkspaceSkillRepository | None = None,
        sql_optimization_service: SqlOptimizationService | None = None,
        jira_service: JiraService | None = None,
        metadata_table_loader: MetadataTableLoader | None = None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None = None,
        metadata_ranking_service: MetadataRankingService | None = None,
        metadata_semantic_grounding_service: MetadataSemanticGroundingService | None = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._support = ToolExecutionSupportKernel(
            metadata_resolver=metadata_resolver,
            metadata_fetcher=metadata_fetcher,
            metadata_table_loader=metadata_table_loader,
            metadata_catalog_search_service=metadata_catalog_search_service,
            metadata_ranking_service=metadata_ranking_service,
            metadata_semantic_grounding_service=metadata_semantic_grounding_service,
            sql_router=sql_router,
            rag_retriever=rag_retriever,
            confluence_connector_repository=confluence_connector_repository,
            jira_connector_repository=jira_connector_repository,
            semantic_preflight_service=semantic_preflight_service,
            sql_optimization_service=(
                sql_optimization_service
                or SqlOptimizationService(
                    workspace_skill_repository=workspace_skill_repository
                )
            ),
            jira_service=jira_service or JiraService(),
        )

        self._metadata_tools = ToolMetadataCapabilityService(
            metadata_table_loader=self._support.metadata_table_loader,
            metadata_catalog_search_service=self._support.metadata_catalog_search_service,
            metadata_ranking_service=self._support.metadata_ranking_service,
            metadata_semantic_grounding_service=self._support.metadata_semantic_grounding_service,
            connection_service=self._support.metadata_connection_service,
            fallback_service=self._support.metadata_fallback_service,
            context_service=self._support.metadata_context_service,
            runtime_lookup_service=self._support.metadata_runtime_lookup_service,
            tool_error_from_exception=self._support._tool_error_from_exception,
            sql_router=sql_router,
        )
        self._sql_tools = ToolSqlCapabilityService(
            support=self._support,
            sql_router=sql_router,
            semantic_preflight_service=self._support.semantic_preflight_service,
            sql_optimization_service=self._support.sql_optimization_service,
            logger=self._logger,
        )
        self._docs_tools = ToolDocsCapabilityService(
            support=self._support,
            rag_retriever=rag_retriever,
            confluence_connector_repository=confluence_connector_repository,
            search_confluence_pages=lambda **kwargs: self._search_confluence_pages(
                **kwargs
            ),
        )
        self._jira_tools = ToolJiraCapabilityService(
            jira_connector_repository=jira_connector_repository,
            jira_service=self._support.jira_service,
            metadata_catalog_search_service=self._support.metadata_catalog_search_service,
            metadata_connection_service=self._support.metadata_connection_service,
            tool_error_from_exception=self._support._tool_error_from_exception,
        )
        self._profile_tools = ToolProfileCapabilityService(
            sql_router=sql_router,
            logger=self._logger,
        )
        self._visualization_tools = ToolVisualizationCapabilityService()
        self._support.bind_sql_optimizer_hooks(
            on_start=lambda **kwargs: self._emit_sql_optimizer_start(**kwargs),
            on_end=lambda **kwargs: self._emit_sql_optimizer_end(**kwargs),
        )
        self._capability_registry = ToolCapabilityRegistry(
            [
                BoundToolCapability(
                    name="fetch_metadata_tables",
                    handler=self._execute_fetch_metadata_tables,
                    requires_db_connectors=True,
                ),
                BoundToolCapability(
                    name="search_metadata_catalog",
                    handler=self._execute_search_metadata_catalog,
                    requires_db_connectors=True,
                ),
                BoundToolCapability(
                    name="get_metadata_entity_details",
                    handler=self._execute_get_metadata_entity_details,
                    requires_db_connectors=True,
                ),
                BoundToolCapability(
                    name="get_metadata_entity_lineage",
                    handler=self._execute_get_metadata_entity_lineage,
                    requires_db_connectors=True,
                ),
                BoundToolCapability(
                    name="run_sql",
                    handler=self._execute_run_sql,
                    requires_db_connectors=True,
                ),
                BoundToolCapability(
                    name="build_visualization",
                    handler=self._execute_build_visualization,
                    requires_db_connectors=True,
                ),
                BoundToolCapability(
                    name="profile_table",
                    handler=self._execute_profile_table,
                    requires_db_connectors=True,
                ),
                BoundToolCapability(
                    name="profile_metric",
                    handler=self._execute_profile_metric,
                    requires_db_connectors=True,
                ),
                BoundToolCapability(
                    name="search_rag_insights",
                    handler=self._execute_search_rag_insights,
                ),
                BoundToolCapability(
                    name="search_confluence_pages",
                    handler=self._execute_search_confluence_pages,
                ),
                BoundToolCapability(
                    name="search_jira_issues",
                    handler=self._execute_search_jira_issues,
                ),
                BoundToolCapability(
                    name="get_jira_issue",
                    handler=self._execute_get_jira_issue,
                ),
                BoundToolCapability(
                    name="create_jira_issue",
                    handler=self._execute_create_jira_issue,
                ),
            ]
        )

    def set_sql_optimizer_event_handlers(
        self,
        *,
        on_start: Callable[[dict[str, Any]], None] | None = None,
        on_end: Callable[[OptimizationTrace, str], None] | None = None,
    ) -> None:
        self._sql_tools.set_sql_optimizer_event_handlers(
            on_start=on_start,
            on_end=on_end,
        )

    def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        tool_request = ToolCallRequest.model_validate(
            {"tool_name": tool_name, "arguments": arguments}
        )
        capability = self._capability_registry.get(tool_request.tool_name)
        if capability is not None:
            result = capability.execute(
                request=request,
                arguments=tool_request.arguments,
                state=state,
                db_connectors=db_connectors,
            )
            self._record_tool_usage(
                tool_name=tool_request.tool_name,
                arguments=tool_request.arguments,
                request=request,
                result=result,
                state=state,
            )
            return result

        self._logger.warning("Unknown tool requested: %s", tool_name)
        state.add_warning("llm_unknown_tool_requested")
        result = build_tool_error(
            error_code="unknown_tool",
            error_category=ErrorCategory.RECOVERABLE,
            user_message=f"Unknown tool requested: {tool_name}.",
            retry_recommended=False,
            can_continue=True,
        )
        result["tool_name"] = tool_name
        self._record_tool_usage(
            tool_name=tool_name,
            arguments=arguments,
            request=request,
            result=result,
            state=state,
        )
        return result

    def _execute_fetch_metadata_tables(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._metadata_tools.execute_fetch_metadata_tables(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def _execute_search_metadata_catalog(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._metadata_tools.execute_search_metadata_catalog(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def _execute_get_metadata_entity_details(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._metadata_tools.execute_get_metadata_entity_details(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def _execute_get_metadata_entity_lineage(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._metadata_tools.execute_get_metadata_entity_lineage(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def _execute_run_sql(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._sql_tools.execute_run_sql(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def _execute_build_visualization(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._visualization_tools.execute_build_visualization(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def _execute_profile_table(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._profile_tools.execute_profile_table(
            request=request,
            arguments=arguments,
            state=state,
            db_connectors=db_connectors,
        )

    def _execute_profile_metric(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._profile_tools.execute_profile_metric(
            request=request,
            arguments=arguments,
            state=state,
            db_connectors=db_connectors,
        )

    def _emit_sql_optimizer_start(
        self,
        *,
        selected_connector_name: str,
        engine: str,
        referenced_tables: list[str],
    ) -> None:
        self._sql_tools.emit_sql_optimizer_start(
            selected_connector_name=selected_connector_name,
            engine=engine,
            referenced_tables=referenced_tables,
        )

    def _emit_sql_optimizer_end(self, *, trace: OptimizationTrace) -> None:
        self._sql_tools.emit_sql_optimizer_end(trace=trace)

    def _execute_search_rag_insights(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._docs_tools.execute_search_rag_insights(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_search_confluence_pages(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._docs_tools.execute_search_confluence_pages(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _search_confluence_pages(
        self,
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        return self._docs_tools._search_confluence_pages(**kwargs)

    def _execute_search_jira_issues(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._jira_tools.execute_search_jira_issues(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_jira_issue(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._jira_tools.execute_get_jira_issue(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_create_jira_issue(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._jira_tools.execute_create_jira_issue(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _record_tool_usage(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        result: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> None:
        providers = self._resolve_providers_for_tool_usage(
            tool_name=tool_name,
            arguments=arguments,
            request=request,
            result=result,
        )
        register_tool_usage(
            runtime_state=state.capability_state,
            tool_name=tool_name,
            providers=providers,
        )

    @classmethod
    def _resolve_providers_for_tool_usage(
        cls,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        result: dict[str, Any],
    ) -> list[str]:
        if tool_name == "run_sql":
            connector_name = (
                result.get("selected_database_connector")
                or arguments.get("connector_name")
                or request.database_connector_name
            )
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"database:{connector_name.strip()}"]
            return ["database"]

        if tool_name in {"profile_table", "profile_metric"}:
            connector_name = arguments.get("connector_name") or request.database_connector_name
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"database:{connector_name.strip()}"]
            return ["database"]

        if tool_name == "build_visualization":
            return ["visualization"]

        if tool_name == "search_rag_insights":
            connector_name = arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"rag:{connector_name.strip()}"]
            return ["rag"]

        if tool_name == "search_confluence_pages":
            connector_name = arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"confluence:{connector_name.strip()}"]
            return ["confluence"]

        if tool_name in {"search_jira_issues", "get_jira_issue", "create_jira_issue"}:
            connector_name = arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"jira:{connector_name.strip()}"]
            return ["jira"]

        providers: list[str] = []
        connector_name = arguments.get("connector_name") or request.metadata_connector_name
        if isinstance(connector_name, str) and connector_name.strip():
            providers.append(f"openmetadata:{connector_name.strip()}")
        else:
            providers.append("openmetadata")

        metadata_sources = result.get("metadata_sources")
        if isinstance(metadata_sources, list):
            if any(source == "mcp" for source in metadata_sources if isinstance(source, str)):
                providers.append("mcp")
            if any(
                source == "runtime" for source in metadata_sources if isinstance(source, str)
            ):
                providers.append("database_runtime")

        tables = result.get("tables")
        if isinstance(tables, list) and any(
            isinstance(item, dict) and item.get("source") == "runtime"
            for item in tables
        ):
            providers.append("database_runtime")

        return providers
