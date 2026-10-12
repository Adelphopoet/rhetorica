"""Live database schema lookup used by guarded SQL execution."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import ExecuteSqlResponse, sql_dialect_for_engine
from rhetorica_backend.domain.chat.metadata import TableContext
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_sql_policy import (
    ToolExecutionRuntimeSqlPolicy,
)


class MetadataRuntimeLookupService:
    """Execute live table lookup against the selected database connector."""

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
        search: str,
        limit: int,
    ) -> list[TableContext]:
        if not isinstance(search, str) or not search.strip() or not db_connectors:
            return []

        selected_connector = self._runtime_sql_policy.select_runtime_lookup_connector(
            request=request,
            db_connectors=db_connectors,
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
        default_schema = None
        if connection.engine == "impala":
            default_schema = self._runtime_sql_policy.runtime_lookup_schema_name(
                connection=connection,
                database_hint=database_hint,
            )

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
        found_tables = self._extract_runtime_table_hits(
            tables_execution,
            default_schema=default_schema,
        )
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
            found_tables = self._extract_runtime_table_hits(
                tables_execution,
                default_schema=default_schema,
            )
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
                    service=selected_connector.name,
                    database=(connection.catalog or connection.database),
                    schema=schema_name,
                )
            )
        return runtime_tables

    @staticmethod
    def _extract_runtime_table_hits(
        execution: ExecuteSqlResponse,
        *,
        default_schema: str | None = None,
    ) -> list[tuple[str, str]]:
        if execution.status != "succeeded":
            return []

        found_tables: list[tuple[str, str]] = []
        for row in execution.preview.rows:
            if not isinstance(row, list) or not row:
                continue
            if len(row) >= 2:
                database = str(row[0]).strip()
                table_name = str(row[1]).strip()
            elif default_schema:
                database = default_schema
                table_name = str(row[0]).strip()
            else:
                continue
            if not database or not table_name:
                continue
            found_tables.append((database, table_name))
        return found_tables
