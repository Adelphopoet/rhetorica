"""Composable support kernel for tool execution helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.jira.service import JiraService
from rhetorica_backend.domain.chat.metadata import (
    MetadataCatalogSearchService,
    MetadataFetcher,
    MetadataRankingService,
    MetadataResolver,
    MetadataSemanticGroundingService,
    MetadataTableLoader,
    TableContext,
    resolve_metadata_runtime_services,
)
from rhetorica_backend.domain.chat.orchestration.metadata_tool_support_services import (
    MetadataRuntimeLookupService,
    MetadataToolConnectionService,
    MetadataToolContextService,
    MetadataToolFallbackService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_runtime_service import (
    ToolExecutionMetadataRuntimeService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_support import (
    ToolExecutionRuntimeSupport,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_sql_schema_hydration_service import (
    ToolExecutionSqlSchemaHydrationService,
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


class ToolExecutionSupportKernel(ToolExecutionRuntimeSupport):
    """Composition root for focused tool-execution collaborators."""

    def __init__(
        self,
        *,
        metadata_resolver: MetadataResolver,
        metadata_fetcher: MetadataFetcher | None,
        metadata_table_loader: MetadataTableLoader | None = None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None = None,
        metadata_ranking_service: MetadataRankingService | None = None,
        metadata_semantic_grounding_service: MetadataSemanticGroundingService | None = None,
        sql_router: SqlRouter,
        rag_retriever: RagInsightRetriever | None = None,
        confluence_connector_repository: ConfluenceConnectorRepository | None = None,
        jira_connector_repository: JiraConnectorRepository | None = None,
        semantic_preflight_service: SemanticSqlPreflightService | None = None,
        sql_optimization_service: SqlOptimizationService | None = None,
        jira_service: JiraService | None = None,
    ) -> None:
        self._metadata_resolver = metadata_resolver
        metadata_services = resolve_metadata_runtime_services(
            metadata_fetcher=metadata_fetcher,
            metadata_table_loader=metadata_table_loader,
            metadata_catalog_search_service=metadata_catalog_search_service,
            metadata_ranking_service=metadata_ranking_service,
            metadata_semantic_grounding_service=metadata_semantic_grounding_service,
        )
        self._metadata_table_loader = metadata_services.table_loader
        self._metadata_catalog_search_service = metadata_services.catalog_search_service
        self._metadata_ranking_service = metadata_services.ranking_service
        self._metadata_semantic_grounding_service = (
            metadata_services.semantic_grounding_service
        )
        self._sql_router = sql_router
        self._rag_retriever = rag_retriever
        self._confluence_connector_repository = confluence_connector_repository
        self._jira_connector_repository = jira_connector_repository
        self._semantic_preflight_service = (
            semantic_preflight_service or SemanticSqlPreflightService()
        )
        self._sql_optimization_service = sql_optimization_service
        self._jira_service = jira_service or JiraService()
        self._metadata_state_context_service = self._metadata_context_service
        self._metadata_runtime_service = ToolExecutionMetadataRuntimeService(
            metadata_resolver=self._metadata_resolver,
            metadata_table_loader=self._metadata_table_loader,
            metadata_catalog_search_service=self._metadata_catalog_search_service,
            metadata_ranking_service=self._metadata_ranking_service,
            connector_scope_policy=self._connector_scope_policy,
            metadata_context_service=self._metadata_state_context_service,
            runtime_sql_policy=self._runtime_sql_policy,
            context_hydration_helpers=self._context_hydration_helpers,
            lookup_runtime_tables=lambda **kwargs: self._lookup_runtime_tables(**kwargs),
        )
        self._metadata_connection_service = MetadataToolConnectionService(
            metadata_runtime_service=self._metadata_runtime_service,
            metadata_context_service=self._metadata_state_context_service,
            runtime_sql_policy=self._runtime_sql_policy,
        )
        self._metadata_fallback_service = MetadataToolFallbackService(
            connector_scope_policy=self._connector_scope_policy,
            metadata_context_service=self._metadata_state_context_service,
            context_hydration_helpers=self._context_hydration_helpers,
        )
        self._metadata_context_service = MetadataToolContextService(
            connector_scope_policy=self._connector_scope_policy,
            metadata_context_service=self._metadata_state_context_service,
            context_hydration_helpers=self._context_hydration_helpers,
            metadata_semantic_grounding_service=self._metadata_semantic_grounding_service,
        )
        self._metadata_runtime_lookup_service = MetadataRuntimeLookupService(
            runtime_sql_policy=self._runtime_sql_policy,
            sql_router=self._sql_router,
        )
        self._sql_schema_hydration_service = ToolExecutionSqlSchemaHydrationService(
            semantic_preflight_service=self._semantic_preflight_service,
            metadata_runtime_service=self._metadata_runtime_service,
            metadata_context_service=self._metadata_state_context_service,
            runtime_sql_policy=self._runtime_sql_policy,
            metadata_catalog_search_service=self._metadata_catalog_search_service,
            table_context_from_entity_result=(
                self._context_hydration_helpers.table_context_from_entity_result
            ),
        )
        self._sql_optimizer_start: Callable[..., None] | None = None
        self._sql_optimizer_end: Callable[..., None] | None = None
        self._confluence_search: Callable[..., tuple[list[dict[str, Any]], list[str]]] | None = None

    @property
    def metadata_table_loader(self) -> MetadataTableLoader | None:
        return self._metadata_table_loader

    @property
    def metadata_catalog_search_service(self) -> MetadataCatalogSearchService | None:
        return self._metadata_catalog_search_service

    @property
    def metadata_ranking_service(self) -> MetadataRankingService | None:
        return self._metadata_ranking_service

    @property
    def metadata_semantic_grounding_service(
        self,
    ) -> MetadataSemanticGroundingService | None:
        return self._metadata_semantic_grounding_service

    @property
    def metadata_connection_service(self) -> MetadataToolConnectionService:
        return self._metadata_connection_service

    @property
    def metadata_fallback_service(self) -> MetadataToolFallbackService:
        return self._metadata_fallback_service

    @property
    def metadata_context_service(self) -> MetadataToolContextService:
        return self._metadata_context_service

    @property
    def metadata_runtime_lookup_service(self) -> MetadataRuntimeLookupService:
        return self._metadata_runtime_lookup_service

    @property
    def semantic_preflight_service(self) -> SemanticSqlPreflightService:
        return self._semantic_preflight_service

    @property
    def sql_optimization_service(self) -> SqlOptimizationService | None:
        return self._sql_optimization_service

    @property
    def jira_service(self) -> JiraService:
        return self._jira_service

    def bind_sql_optimizer_hooks(
        self,
        *,
        on_start: Callable[..., None],
        on_end: Callable[..., None],
    ) -> None:
        self._sql_optimizer_start = on_start
        self._sql_optimizer_end = on_end

    def bind_confluence_search(
        self,
        search_pages: Callable[..., tuple[list[dict[str, Any]], list[str]]],
    ) -> None:
        self._confluence_search = search_pages

    def _lookup_runtime_tables(
        self,
        *,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        metadata_connection: Any | None,
        search: str,
        limit: int,
    ) -> list[TableContext]:
        return self._metadata_runtime_lookup_service.lookup_tables(
            request=request,
            db_connectors=db_connectors,
            metadata_connection=metadata_connection,
            search=search,
            limit=limit,
        )

    def _emit_sql_optimizer_start(self, **kwargs: Any) -> None:
        if self._sql_optimizer_start is None:
            return
        self._sql_optimizer_start(**kwargs)

    def _emit_sql_optimizer_end(self, **kwargs: Any) -> None:
        if self._sql_optimizer_end is None:
            return
        self._sql_optimizer_end(**kwargs)

    def _search_confluence_pages(
        self,
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if self._confluence_search is None:
            return [], []
        return self._confluence_search(**kwargs)

    def _record_successful_metadata_usage(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        referenced_tables: list[str],
        state: Any,
    ) -> None:
        if self._metadata_ranking_service is None:
            return
        table_fqns = self._successful_usage_table_fqns(
            referenced_tables=referenced_tables,
            state=state,
        )
        if not table_fqns:
            return
        record_successful_usage = getattr(
            self._metadata_ranking_service,
            "record_successful_usage",
            None,
        )
        if not callable(record_successful_usage):
            return
        record_successful_usage(
            workspace_id=workspace_id,
            connector_name=connector_name,
            table_fqns=table_fqns,
        )

    @staticmethod
    def _successful_usage_table_fqns(
        *,
        referenced_tables: list[str],
        state: Any,
    ) -> list[str]:
        selected: list[str] = []
        semantic_grounding = getattr(state, "semantic_grounding", None)
        chosen_table = getattr(semantic_grounding, "chosen_table_fqn", None)
        if isinstance(chosen_table, str) and chosen_table.strip():
            selected.append(chosen_table.strip())

        context_tables = [
            table_fqn
            for table_fqn in getattr(state, "context_tables", [])
            if isinstance(table_fqn, str) and table_fqn.strip()
        ]
        referenced_keys = {
            key
            for table in referenced_tables
            for key in ToolExecutionSupportKernel._table_match_keys(table)
        }
        for table_fqn in context_tables:
            table_keys = ToolExecutionSupportKernel._table_match_keys(table_fqn)
            if referenced_keys and referenced_keys.isdisjoint(table_keys):
                continue
            selected.append(table_fqn.strip())
        return ToolExecutionSupportKernel._dedupe_table_fqns(selected)

    @staticmethod
    def _table_match_keys(table_fqn: str) -> set[str]:
        value = table_fqn.strip().lower()
        if not value:
            return set()
        parts = [part for part in value.split(".") if part]
        keys = {value}
        if parts:
            keys.add(parts[-1])
        if len(parts) >= 2:
            keys.add(".".join(parts[-2:]))
        if len(parts) >= 3:
            keys.add(".".join(parts[-3:]))
        return keys

    @staticmethod
    def _dedupe_table_fqns(table_fqns: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for table_fqn in table_fqns:
            key = table_fqn.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(table_fqn)
        return deduped
