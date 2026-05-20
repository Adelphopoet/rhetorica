"""Metadata-family orchestration tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    retry_idempotent,
)
from rhetorica_backend.domain.chat.metadata import (
    MetadataCatalogSearchService,
    MetadataFetcher,
    MetadataRankingService,
    MetadataSemanticGroundingService,
    MetadataTableLoader,
    resolve_metadata_runtime_services,
)
from rhetorica_backend.domain.chat.orchestration.metadata_tool_support_services import (
    MetadataRuntimeLookupService,
    MetadataToolConnectionService,
    MetadataToolContextService,
    MetadataToolFallbackService,
)
from rhetorica_backend.domain.chat.orchestration.metadata_tool_use_cases import (
    FetchMetadataTablesUseCase,
    SearchMetadataCatalogUseCase,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import (
        ToolOrchestrationState,
    )


class ToolMetadataCapabilityService:
    """Capability-family service for metadata tools."""

    def __init__(
        self,
        *,
        metadata_table_loader: MetadataTableLoader | None = None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None = None,
        metadata_ranking_service: MetadataRankingService | None = None,
        metadata_semantic_grounding_service: MetadataSemanticGroundingService | None = None,
        connection_service: MetadataToolConnectionService,
        fallback_service: MetadataToolFallbackService,
        context_service: MetadataToolContextService,
        runtime_lookup_service: MetadataRuntimeLookupService,
        tool_error_from_exception: Callable[..., dict[str, Any]],
        metadata_fetcher: MetadataFetcher | None = None,
        sql_router: SqlRouter | None = None,
    ) -> None:
        metadata_services = resolve_metadata_runtime_services(
            metadata_fetcher=metadata_fetcher,
            metadata_table_loader=metadata_table_loader,
            metadata_catalog_search_service=metadata_catalog_search_service,
            metadata_ranking_service=metadata_ranking_service,
            metadata_semantic_grounding_service=metadata_semantic_grounding_service,
        )
        self._metadata_catalog_search_service = metadata_services.catalog_search_service
        self._connection_service = connection_service
        self._context_service = context_service
        self._tool_error_from_exception = tool_error_from_exception
        self._fetch_metadata_tables = FetchMetadataTablesUseCase(
            metadata_table_loader=metadata_services.table_loader,
            metadata_catalog_search_service=metadata_services.catalog_search_service,
            metadata_ranking_service=metadata_services.ranking_service,
            metadata_semantic_grounding_service=(
                metadata_services.semantic_grounding_service
            ),
            connection_service=connection_service,
            fallback_service=fallback_service,
            context_service=context_service,
            runtime_lookup_service=runtime_lookup_service,
            tool_error_from_exception=tool_error_from_exception,
        )
        self._search_metadata_catalog = SearchMetadataCatalogUseCase(
            metadata_table_loader=metadata_services.table_loader,
            metadata_catalog_search_service=metadata_services.catalog_search_service,
            metadata_ranking_service=metadata_services.ranking_service,
            connection_service=connection_service,
            fallback_service=fallback_service,
            context_service=context_service,
            runtime_lookup_service=runtime_lookup_service,
            tool_error_from_exception=tool_error_from_exception,
        )
        _ = sql_router

    def execute_fetch_metadata_tables(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._fetch_metadata_tables.execute(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def execute_search_metadata_catalog(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._search_metadata_catalog.execute(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def execute_get_metadata_entity_details(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        if self._metadata_catalog_search_service is None:
            state.warnings.append("metadata_entity_details_failed")
            return build_tool_error(
                error_code="metadata_entity_details_failed",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metadata fetcher is not configured.",
                retry_recommended=False,
                can_continue=True,
            )

        connection = self._connection_service.resolve_connection(
            request=request,
            arguments=arguments,
            warnings=state.warnings,
            db_connectors=db_connectors,
        )
        if connection is None:
            state.warnings.append("metadata_connector_missing")
            return build_tool_error(
                error_code="metadata_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metadata connector is missing.",
                retry_recommended=False,
                can_continue=True,
            )
        if not connection.mcp_tables_url:
            state.warnings.append("metadata_mcp_unconfigured")
            return build_tool_error(
                error_code="metadata_mcp_unconfigured",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metadata MCP endpoint is not configured.",
                retry_recommended=False,
                can_continue=True,
            )

        entity_type = arguments.get("entity_type")
        fqn = arguments.get("fqn")
        if entity_type is None or fqn is None:
            return build_tool_error(
                error_code="metadata_entity_params_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="entity_type and fqn are required.",
                retry_recommended=False,
                can_continue=True,
            )

        try:
            result, warnings = retry_idempotent(
                lambda: self._metadata_catalog_search_service.get_entity_details(
                    connection=connection,
                    entity_type=entity_type,
                    fqn=fqn,
                ),
                max_attempts=3,
            )
        except Exception as exc:
            state.warnings.append("metadata_entity_details_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="metadata_entity_details_failed",
                can_continue=True,
            )

        state.warnings.extend(warnings)
        state.metadata_sources.add("mcp")
        self._context_service.hydrate_entity_details(
            state=state,
            entity_type=entity_type,
            result=result,
        )
        if result:
            return {"ok": True, "entity": result}
        return build_tool_error(
            error_code="entity_not_found",
            error_category=ErrorCategory.RECOVERABLE,
            user_message="Entity not found.",
            retry_recommended=False,
            can_continue=True,
        )

    def execute_get_metadata_entity_lineage(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        if self._metadata_catalog_search_service is None:
            state.warnings.append("metadata_entity_lineage_failed")
            return build_tool_error(
                error_code="metadata_entity_lineage_failed",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metadata fetcher is not configured.",
                retry_recommended=False,
                can_continue=True,
            )

        connection = self._connection_service.resolve_connection(
            request=request,
            arguments=arguments,
            warnings=state.warnings,
            db_connectors=db_connectors,
        )
        if connection is None:
            state.warnings.append("metadata_connector_missing")
            return build_tool_error(
                error_code="metadata_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metadata connector is missing.",
                retry_recommended=False,
                can_continue=True,
            )
        if not connection.mcp_tables_url:
            state.warnings.append("metadata_mcp_unconfigured")
            return build_tool_error(
                error_code="metadata_mcp_unconfigured",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metadata MCP endpoint is not configured.",
                retry_recommended=False,
                can_continue=True,
            )

        entity_type = arguments.get("entity_type")
        fqn = arguments.get("fqn")
        if entity_type is None or fqn is None:
            return build_tool_error(
                error_code="metadata_entity_params_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="entity_type and fqn are required.",
                retry_recommended=False,
                can_continue=True,
            )

        try:
            result, warnings = retry_idempotent(
                lambda: self._metadata_catalog_search_service.get_entity_lineage(
                    connection=connection,
                    entity_type=entity_type,
                    fqn=fqn,
                    upstream_depth=arguments.get("upstream_depth", 2),
                    downstream_depth=arguments.get("downstream_depth", 2),
                ),
                max_attempts=3,
            )
        except Exception as exc:
            state.warnings.append("metadata_entity_lineage_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="metadata_entity_lineage_failed",
                can_continue=True,
            )

        state.warnings.extend(warnings)
        state.metadata_sources.add("mcp")
        self._context_service.hydrate_lineage(
            state=state,
            fqn=str(fqn),
            payload=result,
        )
        if result:
            return {"ok": True, "lineage": result}
        return build_tool_error(
            error_code="lineage_not_found",
            error_category=ErrorCategory.RECOVERABLE,
            user_message="Lineage not found.",
            retry_recommended=False,
            can_continue=True,
        )
