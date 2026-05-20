"""Explicit metadata tool use cases."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    retry_idempotent,
)
from rhetorica_backend.domain.chat.metadata import (
    MetadataCatalogSearchService,
    MetadataRankingService,
    MetadataSemanticGroundingService,
    MetadataTableLoader,
    OpenMetadataGovernanceEnrichmentService,
    TableContext,
)
from rhetorica_backend.domain.chat.orchestration.metadata_tool_support_services import (
    MetadataRuntimeLookupService,
    MetadataToolConnectionService,
    MetadataToolContextService,
    MetadataToolFallbackService,
)


class FetchMetadataTablesUseCase:
    """Fetches metadata tables and applies controlled fallback flows."""

    def __init__(
        self,
        *,
        metadata_table_loader: MetadataTableLoader | None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None,
        metadata_ranking_service: MetadataRankingService | None,
        metadata_semantic_grounding_service: MetadataSemanticGroundingService | None,
        connection_service: MetadataToolConnectionService,
        fallback_service: MetadataToolFallbackService,
        context_service: MetadataToolContextService,
        runtime_lookup_service: MetadataRuntimeLookupService,
        tool_error_from_exception: Callable[..., dict[str, Any]],
        governance_enrichment_service: OpenMetadataGovernanceEnrichmentService | None = None,
    ) -> None:
        self._metadata_table_loader = metadata_table_loader
        self._metadata_catalog_search_service = metadata_catalog_search_service
        self._metadata_ranking_service = metadata_ranking_service
        self._metadata_semantic_grounding_service = metadata_semantic_grounding_service
        self._connection_service = connection_service
        self._fallback_service = fallback_service
        self._context_service = context_service
        self._runtime_lookup_service = runtime_lookup_service
        self._tool_error_from_exception = tool_error_from_exception
        self._governance_enrichment_service = (
            governance_enrichment_service or OpenMetadataGovernanceEnrichmentService()
        )

    def execute(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: Any,
    ) -> dict[str, Any]:
        if (
            self._metadata_table_loader is None
            or self._metadata_ranking_service is None
            or self._metadata_catalog_search_service is None
            or self._metadata_semantic_grounding_service is None
        ):
            state.warnings.append("metadata_fetch_failed")
            return build_tool_error(
                error_code="metadata_fetch_failed",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metadata fetcher is not configured.",
                retry_recommended=False,
                can_continue=True,
            )

        connection = self._connection_service.resolve_effective_connection(
            request=request,
            arguments=arguments,
            warnings=state.warnings,
            db_connectors=db_connectors,
            state=state,
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

        connector_name = self._connection_service.connector_name(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
        )
        try:
            tables, metadata_warnings = retry_idempotent(
                lambda: self._metadata_table_loader.fetch_tables(
                    connection=connection,
                    workspace_id=request.workspace_id,
                    connector_name=connector_name if isinstance(connector_name, str) else None,
                ),
                max_attempts=3,
            )
        except Exception as exc:
            state.warnings.append("metadata_fetch_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="metadata_fetch_failed",
                can_continue=True,
            )

        state.warnings.extend(metadata_warnings)
        search = arguments.get("search")
        service = arguments.get("service")
        database = arguments.get("database")
        schema = arguments.get("schema")
        limit = self._connection_service.lookup_limit(
            search=search,
            requested_limit=arguments.get("limit"),
            default=50,
            maximum=200,
        )

        filtered = self._metadata_ranking_service.filter_tables(
            tables=tables,
            search=search,
            service=service,
            database=database,
            schema=schema,
            workspace_id=request.workspace_id,
            connector_name=connector_name if isinstance(connector_name, str) else None,
            top_n=limit,
            preferred_service=getattr(connection, "service_name", None),
            preferred_database=getattr(connection, "database_name", None),
            preferred_schema=getattr(connection, "schema_name", None),
        )
        filtered, enrichment_warnings = self._enrich_and_rerank(
            connection=connection,
            tables=filtered,
            search=search,
            service=service,
            database=database,
            schema=schema,
            workspace_id=request.workspace_id,
            connector_name=connector_name if isinstance(connector_name, str) else None,
            limit=limit,
        )
        metadata_warnings.extend(enrichment_warnings)
        state.warnings.extend(enrichment_warnings)
        semantic_candidates = list(filtered)
        filtered = self._fallback_service.filter_available_tables(
            tables=filtered,
            db_connectors=db_connectors,
        )
        if not filtered and self._fallback_service.should_fallback_fetch_to_catalog(
            search=search,
            connection=connection,
        ):
            filtered, semantic_candidates = self._load_catalog_fallback(
                connection=connection,
                limit=limit,
                search=search,
                db_connectors=db_connectors,
                state=state,
                filtered=filtered,
                semantic_candidates=semantic_candidates,
            )

        had_available_metadata_matches = bool(filtered)
        if self._fallback_service.should_attempt_live_table_lookup(
            search=search,
            table_fqns=[table.fqn for table in filtered],
        ):
            live_tables = self._runtime_lookup_service.lookup_tables(
                request=request,
                db_connectors=db_connectors,
                metadata_connection=connection,
                search=str(search).strip(),
                limit=limit,
            )
            if live_tables:
                filtered = live_tables
                state.warnings.append("metadata_runtime_lookup_used")
                semantic_candidates = self._fallback_service.prefer_semantic_tables(
                    current=semantic_candidates,
                    candidate=live_tables,
                )
        self._context_service.hydrate_fetch_tables(
            state=state,
            connection=connection,
            filtered_tables=filtered,
            semantic_candidates=semantic_candidates,
            search=search,
            db_connectors=db_connectors,
        )

        result_tables = filtered
        if self._fallback_service.should_prefer_semantic_output(search=search):
            visible_semantic_candidates = semantic_candidates
            if had_available_metadata_matches:
                visible_semantic_candidates = self._fallback_service.filter_available_tables(
                    tables=semantic_candidates,
                    db_connectors=db_connectors,
                )
            result_tables = self._fallback_service.prefer_semantic_tables(
                current=filtered,
                candidate=visible_semantic_candidates,
            )

        return {
            "ok": True,
            "count": len(result_tables),
            "metadata_sources": sorted(state.metadata_sources),
            "tables": self._context_service.present_tables(
                tables=result_tables,
                search=search,
                result_count=len(filtered),
            ),
            "warnings": metadata_warnings,
        }

    def _load_catalog_fallback(
        self,
        *,
        connection: Any,
        limit: int,
        search: Any,
        db_connectors: list[ConnectorItem],
        state: Any,
        filtered: list[TableContext],
        semantic_candidates: list[TableContext],
    ) -> tuple[list[TableContext], list[TableContext]]:
        try:
            catalog_results, catalog_warnings = retry_idempotent(
                lambda: self._metadata_catalog_search_service.search_catalog(
                    connection=connection,
                    query=str(search).strip(),
                    entity_type="table",
                    limit=limit,
                    offset=0,
                ),
                max_attempts=3,
            )
            state.warnings.extend(catalog_warnings)
            catalog_tables = [
                table
                for item in catalog_results
                if (table := self._fallback_service.table_context_from_catalog_item(item))
                is not None
            ]
            semantic_candidates = self._fallback_service.prefer_semantic_tables(
                current=semantic_candidates,
                candidate=catalog_tables,
            )
            fallback_tables = self._fallback_service.filter_available_tables(
                tables=catalog_tables,
                db_connectors=db_connectors,
            )
            if fallback_tables:
                filtered = fallback_tables
        except Exception as exc:
            state.warnings.append("metadata_search_failed")
            fallback_error = self._tool_error_from_exception(
                exc,
                default_error_code="metadata_search_failed",
                can_continue=True,
            )
            fallback_warning = fallback_error.get("error_code")
            if isinstance(fallback_warning, str):
                state.warnings.append(fallback_warning)
        return filtered, semantic_candidates

    def _enrich_and_rerank(
        self,
        *,
        connection: Any,
        tables: list[TableContext],
        search: Any,
        service: Any,
        database: Any,
        schema: Any,
        workspace_id: str | None,
        connector_name: str | None,
        limit: int,
    ) -> tuple[list[TableContext], list[str]]:
        if not tables:
            return tables, []
        enriched, warnings = self._governance_enrichment_service.enrich_tables(
            connection=connection,
            tables=tables,
        )
        reranked = self._metadata_ranking_service.filter_tables(
            tables=enriched,
            search=search if isinstance(search, str) else None,
            service=service if isinstance(service, str) else None,
            database=database if isinstance(database, str) else None,
            schema=schema if isinstance(schema, str) else None,
            workspace_id=workspace_id,
            connector_name=connector_name,
            top_n=limit,
            preferred_service=getattr(connection, "service_name", None),
            preferred_database=getattr(connection, "database_name", None),
            preferred_schema=getattr(connection, "schema_name", None),
        )
        return reranked, warnings


class MetadataDiscoveryContractService:
    """Builds LLM-facing discovery semantics from concrete catalog results."""

    def build(
        self,
        *,
        query: str,
        entity_type: str | None,
        service: str | None,
        database: str | None,
        schema: str | None,
        limit: int,
        offset: int,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evidence_gaps = self._evidence_gaps(query=query, results=results)
        has_more = len(results) >= limit
        next_allowed_actions = [
            "get_metadata_entity_details",
            "use_existing_evidence",
        ]
        if has_more:
            next_allowed_actions.append("search_next_page_with_offset")
        next_allowed_actions.extend([
            "search_with_different_terms_or_scope",
            "run_sql_with_proven_sources",
            "ask_clarification",
        ])
        return {
            "search_query": query,
            "entity_type": entity_type,
            "scope": {
                "service": service,
                "database": database,
                "schema": schema,
            },
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
            "new_evidence": True,
            "repeat_same_search_will_add_evidence": False,
            "evidence_gaps": evidence_gaps,
            "next_allowed_actions": next_allowed_actions,
        }

    def _evidence_gaps(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
    ) -> list[str]:
        normalized_query = query.lower()
        asks_store_scope = any(
            token in normalized_query for token in ("магазин", "store", "shop", "warehouse", "whs")
        )
        asks_revenue = any(token in normalized_query for token in ("выруч", "revenue", "gmv"))
        if not (asks_store_scope and asks_revenue):
            return []
        for item in results:
            columns = self._result_columns(item)
            if self._has_store_key(columns) and self._has_revenue_measure(columns):
                return []
        return ["store_level_revenue_source_not_confirmed"]

    @staticmethod
    def _result_columns(item: dict[str, Any]) -> set[str]:
        raw_columns = item.get("columnNames")
        if not isinstance(raw_columns, list):
            raw_columns = item.get("columns")
        columns: set[str] = set()
        if isinstance(raw_columns, list):
            for column in raw_columns:
                if isinstance(column, str):
                    columns.add(column.lower())
                elif isinstance(column, dict):
                    name = column.get("name")
                    if isinstance(name, str):
                        columns.add(name.lower())
        return columns

    @staticmethod
    def _has_store_key(columns: set[str]) -> bool:
        store_keys = {
            "whs_id",
            "whs_code",
            "store_id",
            "store_code",
            "shop_id",
            "shop_code",
            "warehouse_id",
            "warehouse_code",
        }
        return bool(columns & store_keys)

    @staticmethod
    def _has_revenue_measure(columns: set[str]) -> bool:
        revenue_columns = {
            "revenue",
            "net_revenue",
            "gross_revenue",
            "gmv",
            "turnover",
            "amount",
            "sales_amount",
        }
        return bool(columns & revenue_columns)


class SearchMetadataCatalogUseCase:
    """Searches the catalog and falls back to governed/runtine table lookup when needed."""

    def __init__(
        self,
        *,
        metadata_table_loader: MetadataTableLoader | None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None,
        metadata_ranking_service: MetadataRankingService | None,
        connection_service: MetadataToolConnectionService,
        fallback_service: MetadataToolFallbackService,
        context_service: MetadataToolContextService,
        runtime_lookup_service: MetadataRuntimeLookupService,
        tool_error_from_exception: Callable[..., dict[str, Any]],
        governance_enrichment_service: OpenMetadataGovernanceEnrichmentService | None = None,
        discovery_contract_service: MetadataDiscoveryContractService | None = None,
    ) -> None:
        self._metadata_table_loader = metadata_table_loader
        self._metadata_catalog_search_service = metadata_catalog_search_service
        self._metadata_ranking_service = metadata_ranking_service
        self._connection_service = connection_service
        self._fallback_service = fallback_service
        self._context_service = context_service
        self._runtime_lookup_service = runtime_lookup_service
        self._tool_error_from_exception = tool_error_from_exception
        self._governance_enrichment_service = (
            governance_enrichment_service or OpenMetadataGovernanceEnrichmentService()
        )
        self._discovery_contract_service = (
            discovery_contract_service or MetadataDiscoveryContractService()
        )

    def execute(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: Any,
    ) -> dict[str, Any]:
        if (
            self._metadata_table_loader is None
            or self._metadata_ranking_service is None
            or self._metadata_catalog_search_service is None
        ):
            state.warnings.append("metadata_search_failed")
            return build_tool_error(
                error_code="metadata_search_failed",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metadata fetcher is not configured.",
                retry_recommended=False,
                can_continue=True,
            )

        connection = self._connection_service.resolve_effective_connection(
            request=request,
            arguments=arguments,
            warnings=state.warnings,
            db_connectors=db_connectors,
            state=state,
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

        query = arguments.get("query")
        if query is None:
            return build_tool_error(
                error_code="metadata_query_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metadata search query is required.",
                retry_recommended=False,
                can_continue=True,
            )

        entity_type = arguments.get("entity_type")
        service = arguments.get("service")
        database = arguments.get("database")
        schema = arguments.get("schema")
        limit = self._connection_service.lookup_limit(
            search=query,
            requested_limit=arguments.get("limit"),
            default=20,
            maximum=50,
            entity_type=entity_type,
        )
        offset = arguments.get("offset", 0)

        try:
            results, warnings, ranked_results = retry_idempotent(
                lambda: self._search_catalog_with_ranked_results(
                    connection=connection,
                    query=query,
                    entity_type=entity_type,
                    limit=limit,
                    offset=offset,
                    service=service,
                    database=database,
                    schema=schema,
                ),
                max_attempts=3,
            )
        except Exception as exc:
            state.warnings.append("metadata_search_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="metadata_search_failed",
                can_continue=True,
            )

        semantic_candidates = [
            table
            for item in results
            if (table := self._fallback_service.table_context_from_catalog_item(item)) is not None
        ]
        results = self._fallback_service.filter_available_results(
            results=results,
            db_connectors=db_connectors,
        )
        state.warnings.extend(warnings)

        fallback_tables: list[TableContext] = []
        query_text = query.strip() if isinstance(query, str) else ""
        if self._fallback_service.should_fallback_to_table_lookup(
            query=query_text,
            entity_type=entity_type,
            results=results,
        ):
            fallback_tables, semantic_candidates, results = self._load_snapshot_fallback(
                request=request,
                arguments=arguments,
                connection=connection,
                query_text=query_text,
                entity_type=entity_type,
                service=service,
                database=database,
                schema=schema,
                limit=limit,
                db_connectors=db_connectors,
                state=state,
                current_results=results,
                semantic_candidates=semantic_candidates,
            )

        if self._fallback_service.should_fallback_to_table_lookup(
            query=query_text,
            entity_type=entity_type,
            results=results,
        ):
            live_tables = self._runtime_lookup_service.lookup_tables(
                request=request,
                db_connectors=db_connectors,
                metadata_connection=connection,
                search=query_text,
                limit=self._connection_service.bounded_int(
                    limit,
                    default=20,
                    minimum=1,
                    maximum=50,
                ),
            )
            if live_tables:
                results = [
                    self._fallback_service.table_context_to_catalog_result(table)
                    for table in live_tables
                ]
                fallback_tables = live_tables
                state.warnings.append("metadata_runtime_lookup_used")
                semantic_candidates = self._fallback_service.prefer_semantic_tables(
                    current=semantic_candidates,
                    candidate=live_tables,
                )

        self._context_service.hydrate_catalog_search(
            state=state,
            connection=connection,
            results=results,
            fallback_tables=fallback_tables,
            semantic_candidates=semantic_candidates,
            db_connectors=db_connectors,
        )

        result_rows = results
        if self._fallback_service.should_prefer_semantic_output(
            search=query_text,
            entity_type=entity_type,
        ):
            visible_tables = self._fallback_service.prefer_semantic_tables(
                current=fallback_tables,
                candidate=semantic_candidates,
            )
            if visible_tables:
                result_rows = [
                    self._fallback_service.table_context_to_catalog_result(table)
                    for table in visible_tables
                ]

        return {
            "ok": True,
            "metadata_sources": sorted(state.metadata_sources),
            "count": len(result_rows),
            "results": result_rows,
            "ranked_count": len(ranked_results),
            "ranked_results": ranked_results,
            "discovery": self._discovery_contract_service.build(
                query=query_text,
                entity_type=entity_type if isinstance(entity_type, str) else None,
                service=service if isinstance(service, str) else None,
                database=database if isinstance(database, str) else None,
                schema=schema if isinstance(schema, str) else None,
                limit=limit,
                offset=offset if isinstance(offset, int) else 0,
                results=result_rows,
            ),
        }

    def _search_catalog_with_ranked_results(
        self,
        *,
        connection: Any,
        query: Any,
        entity_type: Any,
        limit: int,
        offset: Any,
        service: Any,
        database: Any,
        schema: Any,
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        search_with_ranked_results = getattr(
            self._metadata_catalog_search_service,
            "search_catalog_with_ranked_results",
            None,
        )
        if callable(search_with_ranked_results):
            return search_with_ranked_results(
                connection=connection,
                query=query,
                entity_type=entity_type,
                limit=limit,
                offset=offset,
                service=service,
                database=database,
                schema=schema,
            )
        results, warnings = self._metadata_catalog_search_service.search_catalog(
            connection=connection,
            query=query,
            entity_type=entity_type,
            limit=limit,
            offset=offset,
            service=service,
            database=database,
            schema=schema,
        )
        return results, warnings, []

    def _load_snapshot_fallback(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        connection: Any,
        query_text: str,
        entity_type: Any,
        service: Any,
        database: Any,
        schema: Any,
        limit: int,
        db_connectors: list[ConnectorItem],
        state: Any,
        current_results: list[dict[str, Any]],
        semantic_candidates: list[TableContext],
    ) -> tuple[list[TableContext], list[TableContext], list[dict[str, Any]]]:
        connector_name = self._connection_service.connector_name(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
        )
        results = list(current_results)
        fallback_tables: list[TableContext] = []
        try:
            tables, fallback_warnings = retry_idempotent(
                lambda: self._metadata_table_loader.fetch_tables(
                    connection=connection,
                    workspace_id=request.workspace_id,
                    connector_name=connector_name if isinstance(connector_name, str) else None,
                ),
                max_attempts=3,
            )
            state.warnings.extend(fallback_warnings)
            fallback_tables = self._metadata_ranking_service.filter_tables(
                tables=tables,
                search=query_text,
                service=service if isinstance(service, str) else None,
                database=database if isinstance(database, str) else None,
                schema=schema if isinstance(schema, str) else None,
                workspace_id=request.workspace_id,
                connector_name=connector_name if isinstance(connector_name, str) else None,
                top_n=self._connection_service.bounded_int(
                    limit,
                    default=20,
                    minimum=1,
                    maximum=50,
                ),
                preferred_service=getattr(connection, "service_name", None),
                preferred_database=getattr(connection, "database_name", None),
                preferred_schema=getattr(connection, "schema_name", None),
            )
            fallback_tables, enrichment_warnings = self._enrich_and_rerank(
                connection=connection,
                tables=fallback_tables,
                search=query_text,
                service=service if isinstance(service, str) else None,
                database=database if isinstance(database, str) else None,
                schema=schema if isinstance(schema, str) else None,
                workspace_id=request.workspace_id,
                connector_name=connector_name if isinstance(connector_name, str) else None,
                limit=self._connection_service.bounded_int(
                    limit,
                    default=20,
                    minimum=1,
                    maximum=50,
                ),
            )
            state.warnings.extend(enrichment_warnings)
            semantic_candidates = self._fallback_service.prefer_semantic_tables(
                current=semantic_candidates,
                candidate=fallback_tables,
            )
            fallback_tables = self._fallback_service.filter_available_tables(
                tables=fallback_tables,
                db_connectors=db_connectors,
            )
            if fallback_tables:
                results = [
                    self._fallback_service.table_context_to_catalog_result(table)
                    for table in fallback_tables
                ]
        except Exception as exc:
            state.warnings.append("metadata_fetch_failed")
            fallback_error = self._tool_error_from_exception(
                exc,
                default_error_code="metadata_fetch_failed",
                can_continue=True,
            )
            fallback_warning = fallback_error.get("error_code")
            if isinstance(fallback_warning, str):
                state.warnings.append(fallback_warning)
        return fallback_tables, semantic_candidates, results

    def _enrich_and_rerank(
        self,
        *,
        connection: Any,
        tables: list[TableContext],
        search: Any,
        service: Any,
        database: Any,
        schema: Any,
        workspace_id: str | None,
        connector_name: str | None,
        limit: int,
    ) -> tuple[list[TableContext], list[str]]:
        if not tables:
            return tables, []
        enriched, warnings = self._governance_enrichment_service.enrich_tables(
            connection=connection,
            tables=tables,
        )
        reranked = self._metadata_ranking_service.filter_tables(
            tables=enriched,
            search=search if isinstance(search, str) else None,
            service=service if isinstance(service, str) else None,
            database=database if isinstance(database, str) else None,
            schema=schema if isinstance(schema, str) else None,
            workspace_id=workspace_id,
            connector_name=connector_name,
            top_n=limit,
            preferred_service=getattr(connection, "service_name", None),
            preferred_database=getattr(connection, "database_name", None),
            preferred_schema=getattr(connection, "schema_name", None),
        )
        return reranked, warnings
