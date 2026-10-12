"""Mandatory database schema hydration gate before SQL semantic preflight."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_runtime_service import (
    ToolExecutionMetadataRuntimeService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_sql_policy import (
    ToolExecutionRuntimeSqlPolicy,
)
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
    SqlStatementInspection,
)


@dataclass(frozen=True)
class SqlSchemaHydrationOutcome:
    """Result of the preflight schema hydration gate."""

    inspection: SqlStatementInspection
    context_repaired: bool = False
    hydrated_tables: tuple[str, ...] = ()
    unresolved_tables: tuple[str, ...] = ()


class ToolExecutionSqlSchemaHydrationService:
    """Hydrates referenced schemas by inspecting the selected database."""

    def __init__(
        self,
        *,
        semantic_preflight_service: SemanticSqlPreflightService,
        metadata_runtime_service: ToolExecutionMetadataRuntimeService,
        runtime_sql_policy: ToolExecutionRuntimeSqlPolicy,
        **_: Any,
    ) -> None:
        self._semantic_preflight_service = semantic_preflight_service
        self._metadata_runtime_service = metadata_runtime_service
        self._runtime_sql_policy = runtime_sql_policy

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

        return SqlSchemaHydrationOutcome(
            inspection=inspection,
            context_repaired=context_repaired,
            hydrated_tables=tuple(resolved_targets.values()) if context_repaired else (),
            unresolved_tables=tuple(unresolved_references),
        )

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
        known_tables = list(context_tables) or list(metadata_columns)
        resolved: dict[str, str] = {}
        for table in referenced_tables:
            resolved_table = self._semantic_preflight_service.resolve_table_reference(
                table,
                known_tables,
            )
            if resolved_table is not None:
                resolved[table] = resolved_table
        return resolved
