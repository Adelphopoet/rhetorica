"""Focused support services for metadata tool execution."""

from __future__ import annotations

import re
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import ExecuteSqlResponse, sql_dialect_for_engine
from rhetorica_backend.domain.chat.metadata import (
    MetadataSemanticGroundingService,
    TableContext,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_context_hydration_helpers import (
    ToolExecutionContextHydrationHelpers,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_context_service import (
    ToolExecutionMetadataContextService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_runtime_service import (
    ToolExecutionMetadataRuntimeService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_sql_policy import (
    ToolExecutionRuntimeSqlPolicy,
)


class MetadataToolConnectionService:
    """Resolves metadata connections and normalized connector scope inputs."""

    def __init__(
        self,
        *,
        metadata_runtime_service: ToolExecutionMetadataRuntimeService,
        metadata_context_service: ToolExecutionMetadataContextService,
        runtime_sql_policy: ToolExecutionRuntimeSqlPolicy,
    ) -> None:
        self._metadata_runtime_service = metadata_runtime_service
        self._metadata_context_service = metadata_context_service
        self._runtime_sql_policy = runtime_sql_policy

    def resolve_connection(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        warnings: list[str],
        db_connectors: list[ConnectorItem],
    ) -> Any | None:
        return self._metadata_runtime_service.resolve_metadata_connection(
            request=request,
            arguments=arguments,
            warnings=warnings,
            db_connectors=db_connectors,
        )

    def resolve_effective_connection(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        warnings: list[str],
        db_connectors: list[ConnectorItem],
        state: Any | None,
    ) -> Any | None:
        return self._metadata_runtime_service.resolve_effective_metadata_connection(
            request=request,
            arguments=arguments,
            warnings=warnings,
            db_connectors=db_connectors,
            state=state,
        )

    def connector_name(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> str | None:
        return self._runtime_sql_policy.metadata_tool_connector_name(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
        )

    def lookup_limit(
        self,
        *,
        search: Any,
        requested_limit: Any,
        default: int,
        maximum: int,
        entity_type: Any | None = None,
    ) -> int:
        return self._metadata_context_service.metadata_lookup_limit(
            search=search,
            requested_limit=requested_limit,
            default=default,
            maximum=maximum,
            entity_type=entity_type,
        )

    def bounded_int(
        self,
        value: Any,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        return self._metadata_context_service.bounded_int(
            value,
            default=default,
            minimum=minimum,
            maximum=maximum,
        )


class MetadataToolFallbackService:
    """Owns metadata result filtering and fallback eligibility rules."""

    def __init__(
        self,
        *,
        connector_scope_policy: ToolExecutionConnectorScopePolicy,
        metadata_context_service: ToolExecutionMetadataContextService,
        context_hydration_helpers: ToolExecutionContextHydrationHelpers,
    ) -> None:
        self._connector_scope_policy = connector_scope_policy
        self._metadata_context_service = metadata_context_service
        self._context_hydration_helpers = context_hydration_helpers

    def filter_available_tables(
        self,
        *,
        tables: list[TableContext],
        db_connectors: list[ConnectorItem],
    ) -> list[TableContext]:
        return self._connector_scope_policy.filter_table_contexts_by_available_connectors(
            tables=tables,
            db_connectors=db_connectors,
        )

    def filter_available_results(
        self,
        *,
        results: list[dict[str, Any]],
        db_connectors: list[ConnectorItem],
    ) -> list[dict[str, Any]]:
        return self._connector_scope_policy.filter_catalog_results_by_available_connectors(
            results=results,
            db_connectors=db_connectors,
            table_context_from_entity_result=(
                self._context_hydration_helpers.table_context_from_entity_result
            ),
            normalized_result_entity_type=(
                self._context_hydration_helpers.normalized_result_entity_type
            ),
        )

    def prefer_semantic_tables(
        self,
        *,
        current: list[TableContext],
        candidate: list[TableContext],
    ) -> list[TableContext]:
        return self._metadata_context_service.prefer_semantic_tables(
            current=current,
            candidate=candidate,
        )

    def should_fallback_fetch_to_catalog(
        self,
        *,
        search: Any,
        connection: Any,
    ) -> bool:
        return self._metadata_context_service.should_fallback_fetch_to_catalog(
            search=search,
            connection=connection,
        )

    def should_attempt_live_table_lookup(
        self,
        *,
        search: Any,
        table_fqns: list[str],
    ) -> bool:
        return self._metadata_context_service.should_attempt_live_table_lookup(
            search=search,
            table_fqns=table_fqns,
        )

    def should_fallback_to_table_lookup(
        self,
        *,
        query: str,
        entity_type: Any,
        results: list[dict[str, Any]],
    ) -> bool:
        return self._metadata_context_service.should_fallback_to_table_lookup(
            query=query,
            entity_type=entity_type,
            results=results,
        )

    def should_prefer_semantic_output(
        self,
        *,
        search: Any,
        entity_type: Any | None = None,
    ) -> bool:
        return self._metadata_context_service.should_prefer_semantic_output(
            search=search,
            entity_type=entity_type,
        )

    def table_context_from_catalog_item(
        self,
        item: dict[str, Any],
    ) -> TableContext | None:
        return self._context_hydration_helpers.table_context_from_catalog_item(item)

    def table_context_from_entity_result(
        self,
        result: dict[str, Any] | None,
    ) -> TableContext | None:
        return self._context_hydration_helpers.table_context_from_entity_result(result)

    def table_context_to_catalog_result(
        self,
        table: TableContext,
    ) -> dict[str, Any]:
        return self._context_hydration_helpers.table_context_to_catalog_result(table)

    def strong_catalog_table_fqns(
        self,
        results: list[dict[str, Any]],
    ) -> list[str]:
        return self._metadata_context_service.strong_catalog_table_fqns(results)


class MetadataToolContextService:
    """Hydrates state and presents metadata results back to the tool layer."""

    def __init__(
        self,
        *,
        connector_scope_policy: ToolExecutionConnectorScopePolicy,
        metadata_context_service: ToolExecutionMetadataContextService,
        context_hydration_helpers: ToolExecutionContextHydrationHelpers,
        metadata_semantic_grounding_service: MetadataSemanticGroundingService,
    ) -> None:
        self._connector_scope_policy = connector_scope_policy
        self._metadata_context_service = metadata_context_service
        self._context_hydration_helpers = context_hydration_helpers
        self._metadata_semantic_grounding_service = metadata_semantic_grounding_service

    def hydrate_fetch_tables(
        self,
        *,
        state: Any,
        connection: Any,
        filtered_tables: list[TableContext],
        semantic_candidates: list[TableContext],
        search: Any,
        db_connectors: list[ConnectorItem],
    ) -> None:
        context_tables = self._metadata_context_service.fetch_context_tables(
            search=search,
            filtered=filtered_tables,
            db_connectors=db_connectors,
        )
        if context_tables:
            self._metadata_context_service.merge_context_tables(
                state=state,
                table_fqns=context_tables,
            )
            self._metadata_context_service.prioritize_context_tables(
                state=state,
                preferred_table_fqns=[table.fqn for table in filtered_tables[:5]],
            )
            context_table_set = {table_fqn.lower() for table_fqn in context_tables}
            self._metadata_context_service.update_table_column_state(
                state=state,
                tables=[
                    table
                    for table in filtered_tables
                    if table.fqn.strip().lower() in context_table_set
                ],
            )
            self._metadata_context_service.sync_execution_zone(
                state=state,
                db_connectors=db_connectors,
            )
        state.metadata_sources.update(
            self._metadata_context_service.metadata_source_names(connection, filtered_tables)
        )
        self._metadata_context_service.update_semantic_grounding(
            state=state,
            tables=semantic_candidates or filtered_tables,
        )

    def hydrate_catalog_search(
        self,
        *,
        state: Any,
        connection: Any,
        results: list[dict[str, Any]],
        fallback_tables: list[TableContext],
        semantic_candidates: list[TableContext],
        db_connectors: list[ConnectorItem],
    ) -> None:
        if fallback_tables:
            self._metadata_context_service.merge_context_tables(
                state=state,
                table_fqns=[table.fqn for table in fallback_tables],
            )
            self._metadata_context_service.update_table_column_state(
                state=state,
                tables=fallback_tables,
            )
            self._metadata_context_service.sync_execution_zone(
                state=state,
                db_connectors=db_connectors,
            )
            state.metadata_sources.update(
                self._metadata_context_service.metadata_source_names(
                    connection,
                    fallback_tables,
                )
            )
        elif results:
            table_fqns = self._metadata_context_service.strong_catalog_table_fqns(results)
            table_fqns = self._connector_scope_policy.filter_context_tables_by_available_connectors(
                table_fqns=table_fqns,
                db_connectors=db_connectors,
            )
            self._metadata_context_service.merge_context_tables(
                state=state,
                table_fqns=table_fqns,
            )
            self._metadata_context_service.prioritize_context_tables(
                state=state,
                preferred_table_fqns=table_fqns,
            )
            self._metadata_context_service.sync_execution_zone(
                state=state,
                db_connectors=db_connectors,
            )
            state.metadata_sources.add("mcp")
        else:
            state.metadata_sources.add("mcp")

        if semantic_candidates:
            self._metadata_context_service.update_semantic_grounding(
                state=state,
                tables=semantic_candidates,
            )

    def hydrate_entity_details(
        self,
        *,
        state: Any,
        entity_type: Any,
        result: dict[str, Any] | None,
    ) -> None:
        if entity_type != "table":
            return
        table_context = self._context_hydration_helpers.table_context_from_entity_result(result)
        if table_context is None:
            return
        self._metadata_context_service.merge_context_tables(
            state=state,
            table_fqns=[table_context.fqn],
        )
        self._metadata_context_service.set_table_schema_snapshot(
            state=state,
            table_fqn=table_context.fqn,
            columns={
                column.strip().lower()
                for column in table_context.columns
                if isinstance(column, str) and column.strip()
            },
            complete=True,
            source="entity_details",
            column_types=table_context.column_types,
            column_descriptions=table_context.column_descriptions,
        )
        self._metadata_context_service.update_semantic_grounding(
            state=state,
            tables=[table_context],
            chosen_table_fqn=table_context.fqn,
            preserve_stronger_existing=False,
        )

    def hydrate_lineage(
        self,
        *,
        state: Any,
        fqn: str,
        payload: dict[str, Any] | None,
    ) -> None:
        self._metadata_context_service.set_semantic_lineage_summary(
            state=state,
            fqn=fqn,
            lineage_summary=self._context_hydration_helpers.summarize_lineage_payload(
                payload,
                fqn=fqn,
            ),
        )

    def present_tables(
        self,
        *,
        tables: list[TableContext],
        search: Any,
        result_count: int,
    ) -> list[dict[str, Any]]:
        column_limit = self._tool_result_column_limit(
            search=search,
            result_count=result_count,
        )
        return [
            {
                "source": table.source,
                "fqn": table.fqn,
                "description": table.description,
                "service": table.service,
                "database": table.database,
                "schema": table.schema,
                "owners": table.owners[:10],
                "owner_teams": table.owner_teams[:10],
                "owner_refs": [
                    self._context_hydration_helpers._payload_mapper.entity_ref_payload(ref)
                    for ref in table.owner_refs[:10]
                ],
                "tags": table.tags[:10],
                "domains": table.domains[:10],
                "domain_refs": [
                    self._context_hydration_helpers._payload_mapper.entity_ref_payload(ref)
                    for ref in table.domain_refs[:10]
                ],
                "glossary_terms": [
                    self._context_hydration_helpers._payload_mapper.entity_ref_payload(ref)
                    for ref in table.glossary_terms[:10]
                ],
                "related_metrics": [
                    self._context_hydration_helpers._payload_mapper.metric_ref_payload(ref)
                    for ref in table.related_metrics[:10]
                ],
                "quality_summary": (
                    table.quality_summary.to_payload()
                    if table.quality_summary is not None
                    else None
                ),
                "quality_status": (
                    table.quality_summary.status if table.quality_summary is not None else None
                ),
                "tier": table.tier,
                "certification": table.certification,
                "columns": [
                    {
                        "name": column_name,
                        "type": table.column_types.get(column_name),
                        "description": table.column_descriptions.get(column_name),
                    }
                    for column_name in table.columns[:column_limit]
                ],
                "relevance_score": round(table.relevance_score, 3),
                "matched_on": table.matched_on,
                "reason_codes": self._metadata_semantic_grounding_service.semantic_reason_codes(
                    table
                ),
                "suspicious": self._metadata_semantic_grounding_service.is_suspicious_table(table),
                "usage_count": table.usage_count,
                "usage_frequency": table.usage_frequency,
            }
            for table in tables
        ]

    @staticmethod
    def _tool_result_column_limit(
        *,
        search: Any,
        result_count: int,
    ) -> int:
        if (
            result_count <= 5
            and isinstance(search, str)
            and re.fullmatch(
                r"[a-z0-9_.]+",
                search.strip().lower(),
            )
        ):
            return 200
        return 20


class MetadataRuntimeLookupService:
    """Executes live runtime table lookup against the selected execution connector."""

    def __init__(
        self,
        *,
        runtime_sql_policy: ToolExecutionRuntimeSqlPolicy,
        sql_router: Any,
    ) -> None:
        self._runtime_sql_policy = runtime_sql_policy
        self._sql_router = sql_router

    def lookup_tables(
        self,
        *,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        metadata_connection: Any | None,
        search: str,
        limit: int,
    ) -> list[TableContext]:
        if not isinstance(search, str) or not search.strip() or not db_connectors:
            return []

        selected_connector = self._runtime_sql_policy.select_runtime_lookup_connector(
            request=request,
            db_connectors=db_connectors,
            metadata_connection=metadata_connection,
            sql_router=self._sql_router,
        )
        if selected_connector is None:
            return []

        connection = self._sql_router.get_connection_for_execution(
            workspace_id=request.workspace_id,
            connector_name=selected_connector.name,
        )
        if connection is None or connection.engine not in {
            "clickhouse",
            "trino",
            "greenplum",
            "impala",
        }:
            return []

        leaf_name, database_hint = self._runtime_sql_policy.runtime_lookup_target(search)
        if leaf_name is None:
            return []
        dialect = sql_dialect_for_engine(connection.engine)

        tables_execution = self._sql_router.execute_sql(
            sql=self._runtime_sql_policy.runtime_tables_lookup_sql(
                connection=connection,
                leaf_name=leaf_name,
                database_hint=database_hint,
                limit=limit,
            ),
            connection=connection,
            workspace_id=request.workspace_id,
            dialect=dialect,
        )
        found_tables = self._extract_runtime_table_hits(tables_execution)
        if not found_tables and len(leaf_name) >= 3:
            tables_execution = self._sql_router.execute_sql(
                sql=self._runtime_sql_policy.runtime_tables_lookup_sql(
                    connection=connection,
                    leaf_name=leaf_name,
                    database_hint=database_hint,
                    limit=limit,
                    exact=False,
                ),
                connection=connection,
                workspace_id=request.workspace_id,
                dialect=dialect,
            )
            found_tables = self._extract_runtime_table_hits(tables_execution)
        if not found_tables:
            return []

        runtime_tables: list[TableContext] = []
        for schema_name, table_name in found_tables[:limit]:
            columns_execution = self._sql_router.execute_sql(
                sql=self._runtime_sql_policy.runtime_columns_lookup_sql(
                    connection=connection,
                    schema_name=schema_name,
                    table_name=table_name,
                ),
                connection=connection,
                workspace_id=request.workspace_id,
                dialect=dialect,
            )
            column_names: list[str] = []
            column_types: dict[str, str] = {}
            if columns_execution.status == "succeeded":
                for row in columns_execution.preview.rows:
                    if not isinstance(row, list) or len(row) < 2:
                        continue
                    column_name = str(row[0]).strip()
                    column_type = str(row[1]).strip()
                    if not column_name:
                        continue
                    column_names.append(column_name)
                    if column_type:
                        column_types[column_name] = column_type

            runtime_tables.append(
                TableContext(
                    source="runtime",
                    fqn=self._runtime_sql_policy.runtime_table_fqn(
                        connection=connection,
                        schema_name=schema_name,
                        table_name=table_name,
                    ),
                    columns=column_names,
                    column_types=column_types,
                    service=self._runtime_sql_policy.metadata_service_name(connection),
                    database=self._runtime_sql_policy.metadata_mapping_value(
                        connection=connection,
                        key="metadata_database",
                    ),
                    schema=schema_name,
                )
            )
        return runtime_tables

    @staticmethod
    def _extract_runtime_table_hits(
        execution: ExecuteSqlResponse,
    ) -> list[tuple[str, str]]:
        if execution.status != "succeeded":
            return []

        found_tables: list[tuple[str, str]] = []
        for row in execution.preview.rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            database = str(row[0]).strip()
            table_name = str(row[1]).strip()
            if not database or not table_name:
                continue
            found_tables.append((database, table_name))
        return found_tables
