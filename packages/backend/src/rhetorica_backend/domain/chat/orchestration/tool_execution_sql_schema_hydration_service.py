"""Mandatory schema hydration gate before SQL semantic preflight."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.error_contract import retry_idempotent
from rhetorica_backend.domain.chat.metadata import MetadataCatalogSearchService, TableContext
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_context_service import (
    ToolExecutionMetadataContextService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_runtime_service import (
    ToolExecutionMetadataRuntimeService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_sql_policy import (
    ToolExecutionRuntimeSqlPolicy,
)
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
    SqlStatementInspection,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SqlSchemaHydrationOutcome:
    """Result of the preflight schema hydration gate."""

    inspection: SqlStatementInspection
    context_repaired: bool = False
    hydrated_tables: tuple[str, ...] = ()
    unresolved_tables: tuple[str, ...] = ()


class ToolExecutionSqlSchemaHydrationService:
    """Hydrates full table schemas before SQL preflight without making OMD mandatory."""

    def __init__(
        self,
        *,
        semantic_preflight_service: SemanticSqlPreflightService,
        metadata_runtime_service: ToolExecutionMetadataRuntimeService,
        metadata_context_service: ToolExecutionMetadataContextService,
        runtime_sql_policy: ToolExecutionRuntimeSqlPolicy,
        metadata_catalog_search_service: MetadataCatalogSearchService | None,
        table_context_from_entity_result,
    ) -> None:
        self._semantic_preflight_service = semantic_preflight_service
        self._metadata_runtime_service = metadata_runtime_service
        self._metadata_context_service = metadata_context_service
        self._runtime_sql_policy = runtime_sql_policy
        self._metadata_catalog_search_service = metadata_catalog_search_service
        self._table_context_from_entity_result = table_context_from_entity_result

    def hydrate_for_preflight(
        self,
        *,
        request: ChatAskRequest,
        state: Any,
        selected_connector: ConnectorItem,
        connection: Any,
        sql: str,
        dialect: str,
    ) -> SqlSchemaHydrationOutcome:
        inspection = self._semantic_preflight_service.inspect(sql=sql, dialect=dialect)
        if not inspection.parsed:
            return SqlSchemaHydrationOutcome(inspection=inspection)

        referenced_tables = [
            table
            for table in inspection.referenced_tables
            if not self._semantic_preflight_service.is_builtin_table_reference(table)
        ]
        if not referenced_tables or inspection.is_introspection:
            return SqlSchemaHydrationOutcome(inspection=inspection)

        resolved_targets = self._resolve_referenced_tables(
            state=state,
            selected_connector=selected_connector,
            referenced_tables=referenced_tables,
        )
        unresolved_references = [
            table for table in referenced_tables if table not in resolved_targets
        ]

        context_repaired = False
        if unresolved_references:
            context_repaired = self._metadata_runtime_service.attempt_metadata_context_repair(
                request=request,
                state=state,
                selected_connector=selected_connector,
                connection=connection,
                missing_tables=unresolved_references,
            )
            if context_repaired:
                resolved_targets = self._resolve_referenced_tables(
                    state=state,
                    selected_connector=selected_connector,
                    referenced_tables=referenced_tables,
                )
                unresolved_references = [
                    table for table in referenced_tables if table not in resolved_targets
                ]

        pending_exact_details = [
            table_fqn
            for table_fqn in self._unique_preserving_order(
                self._metadata_detail_targets(
                    selected_connector=selected_connector,
                    resolved_tables=resolved_targets.values(),
                )
            )
            if not self._metadata_context_service.has_complete_table_schema(
                state=state,
                table_fqn=table_fqn,
            )
        ]
        hydrated_tables = self._hydrate_exact_table_details(
            request=request,
            state=state,
            selected_connector=selected_connector,
            pending_tables=pending_exact_details,
        )
        return SqlSchemaHydrationOutcome(
            inspection=inspection,
            context_repaired=context_repaired,
            hydrated_tables=tuple(hydrated_tables),
            unresolved_tables=tuple(unresolved_references),
        )

    def _hydrate_exact_table_details(
        self,
        *,
        request: ChatAskRequest,
        state: Any,
        selected_connector: ConnectorItem,
        pending_tables: list[str],
    ) -> list[str]:
        if not pending_tables or self._metadata_catalog_search_service is None:
            return []

        metadata_connection = self._metadata_runtime_service.resolve_effective_metadata_connection(
            request=request,
            arguments={},
            warnings=state.warnings,
            db_connectors=[selected_connector],
            state=state,
        )
        if metadata_connection is None:
            return []

        worker_count = min(10, len(pending_tables))
        fetched_payloads: dict[str, tuple[dict[str, Any] | None, list[str]]] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_by_table = {
                executor.submit(
                    self._fetch_entity_details,
                    metadata_connection=metadata_connection,
                    table_fqn=table_fqn,
                ): table_fqn
                for table_fqn in pending_tables
            }
            for future in as_completed(future_by_table):
                table_fqn = future_by_table[future]
                try:
                    fetched_payloads[table_fqn] = future.result()
                except Exception:
                    logger.debug(
                        "Exact metadata entity lookup failed during SQL schema hydration",
                        exc_info=True,
                    )
                    fetched_payloads[table_fqn] = (None, ["metadata_entity_details_failed"])

        hydrated_tables: list[str] = []
        for table_fqn in pending_tables:
            payload, warnings = fetched_payloads.get(table_fqn, (None, []))
            for warning in warnings:
                state.add_warning(warning)
            table_context = self._table_context_from_entity_result(payload)
            if table_context is None or not table_context.columns:
                continue
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
            self._mirror_entity_schema_to_execution_refs(
                state=state,
                selected_connector=selected_connector,
                table_context=table_context,
            )
            hydrated_tables.append(table_context.fqn)

        if hydrated_tables:
            self._metadata_context_service.sync_execution_zone(
                state=state,
                db_connectors=[selected_connector],
            )
            state.metadata_sources.update(
                self._metadata_context_service.metadata_source_names(
                    metadata_connection,
                    [
                        TableContext(
                            source="mcp",
                            fqn=table_fqn,
                            columns=[],
                        )
                        for table_fqn in hydrated_tables
                    ],
                )
            )
            if state.selected_database_connector is None:
                state.selected_database_connector = selected_connector.name
        return hydrated_tables

    def _resolve_referenced_tables(
        self,
        *,
        state: Any,
        selected_connector: ConnectorItem,
        referenced_tables: list[str],
    ) -> dict[str, str]:
        context_tables, metadata_columns = self._runtime_sql_policy.preflight_metadata_context(
            state=state,
            selected_connector=selected_connector,
        )
        known_tables = list(context_tables)
        if not known_tables:
            known_tables = list(metadata_columns)
        resolved: dict[str, str] = {}
        for table in referenced_tables:
            resolved_table = self._semantic_preflight_service.resolve_table_reference(
                table,
                known_tables,
            )
            if resolved_table is not None:
                resolved[table] = resolved_table
        return resolved

    @staticmethod
    def _metadata_detail_targets(
        *,
        selected_connector: ConnectorItem,
        resolved_tables: Iterable[str],
    ) -> list[str]:
        targets: list[str] = []
        for table_fqn in resolved_tables:
            target = ToolExecutionConnectorScopePolicy.metadata_table_fqn_for_execution_ref(
                table_fqn=table_fqn,
                selected_connector=selected_connector,
            )
            targets.append(target or table_fqn)
        return targets

    def _mirror_entity_schema_to_execution_refs(
        self,
        *,
        state: Any,
        selected_connector: ConnectorItem,
        table_context: TableContext,
    ) -> None:
        columns = {
            column.strip().lower()
            for column in table_context.columns
            if isinstance(column, str) and column.strip()
        }
        if not columns:
            return
        for execution_ref in ToolExecutionConnectorScopePolicy.execution_refs_for_metadata_table(
            table_fqn=table_context.fqn,
            selected_connector=selected_connector,
        ):
            self._metadata_context_service.merge_context_tables(
                state=state,
                table_fqns=[execution_ref],
            )
            self._metadata_context_service.set_table_schema_snapshot(
                state=state,
                table_fqn=execution_ref,
                columns=columns,
                complete=True,
                source="entity_details",
                column_types=table_context.column_types,
                column_descriptions=table_context.column_descriptions,
            )

    def _fetch_entity_details(
        self,
        *,
        metadata_connection: Any,
        table_fqn: str,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        if self._metadata_catalog_search_service is None:
            return None, []
        try:
            payload, warnings = retry_idempotent(
                lambda: self._metadata_catalog_search_service.get_entity_details(
                    connection=metadata_connection,
                    entity_type="table",
                    fqn=table_fqn,
                ),
                max_attempts=3,
            )
        except Exception:
            logger.debug(
                "Metadata get_entity_details failed during SQL schema hydration",
                exc_info=True,
            )
            return None, ["metadata_entity_details_failed"]
        return payload, warnings

    @staticmethod
    def _unique_preserving_order(values: Iterable[str]) -> list[str]:
        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_values.append(value)
        return unique_values
