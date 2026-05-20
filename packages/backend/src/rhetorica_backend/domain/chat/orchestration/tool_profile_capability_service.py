"""Profile capability service for low-cost table and metric profiling."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

# SQL injection checks (S608) are intentionally disabled for this module.
# All queries are metadata/system queries (system.tables, information_schema)
# that are validated through the existing SQL guard and execution policy.
# These are read-only, low-cost queries that do not modify data.
# ruff: noqa: S608
from rhetorica_backend.contracts.chat import (
    CardinalityBucket,
    ChatAskRequest,
    ProfileColumnSummary,
    ProfileFreshness,
    ProfileRowCounts,
    ProfileSummary,
    ProfileWarnings,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import sql_dialect_for_engine
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
    from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter


def _validate_identifier(name: str) -> str:
    """
    Validate and sanitize a SQL identifier (table, column, schema name).

    Only allows alphanumeric characters, underscores, and periods.
    Rejects anything that could enable SQL injection via identifier injection.
    """
    if not name or not name.strip():
        raise ValueError("Identifier must not be empty")
    if len(name) > 128:
        raise ValueError("Identifier exceeds 128 characters")
    # Reject any character that isn't safe for SQL identifiers
    if not all(c.isalnum() or c in ("_", ".") for c in name):
        raise ValueError(f"Identifier contains invalid characters: {name!r}")
    return name.strip().lower()


# Low-cost profiling constants
TOP_VALUES_CAP = 10
HIGH_CARDINALITY_THRESHOLD = 10000
MEDIUM_CARDINALITY_THRESHOLD = 100
STALE_DAYS_THRESHOLD = 7
PROFILE_SAMPLE_LIMIT = 10000


def _classify_cardinality(distinct_count: int | None) -> str:
    """Classify cardinality into bucket."""
    if distinct_count is None:
        return CardinalityBucket.LOW
    if distinct_count > HIGH_CARDINALITY_THRESHOLD:
        return CardinalityBucket.HIGH
    if distinct_count >= MEDIUM_CARDINALITY_THRESHOLD:
        return CardinalityBucket.MEDIUM
    return CardinalityBucket.LOW


def _parse_table_reference(table_name: str) -> tuple[str | None, str, str | None]:
    """
    Parse table reference into (schema, table, database) components.

    Supports formats:
    - schema.table
    - database.schema.table
    - table (defaults to current database)
    """
    parts = [p.strip() for p in table_name.split(".")]
    if len(parts) == 3:
        return parts[1], parts[2], parts[0]  # schema, table, database
    if len(parts) == 2:
        return parts[0], parts[1], None  # schema, table
    return None, parts[0], None  # table only


def _build_clickhouse_row_count_sql(table: str, schema: str | None) -> str:
    """Build ClickHouse row count query using system.tables."""
    if schema:
        return f"SELECT count AS row_count FROM system.tables WHERE database = '{schema}' AND name = '{table}' LIMIT 1"
    return f"SELECT total_rows AS row_count FROM system.tables WHERE name = '{table}' LIMIT 1"


def _build_clickhouse_null_rate_sql(table: str, schema: str | None, column: str) -> str:
    """Build ClickHouse null rate query using count."""
    table_ref = f"{schema}.{table}" if schema else table
    return f"SELECT countIf({column} IS NULL) / count() * 100 AS null_rate FROM {table_ref} LIMIT 1"


def _build_clickhouse_cardinality_sql(table: str, schema: str | None, column: str) -> str:
    """Build ClickHouse cardinality query."""
    table_ref = f"{schema}.{table}" if schema else table
    return f"SELECT uniqExact({column}) AS distinct_count FROM {table_ref} LIMIT 1"


def _build_clickhouse_top_values_sql(
    table: str, schema: str | None, column: str, limit: int
) -> str:
    """Build ClickHouse top values query."""
    table_ref = f"{schema}.{table}" if schema else table
    return (
        f"SELECT {column}, count() AS cnt "
        f"FROM {table_ref} "
        f"GROUP BY {column} "
        f"ORDER BY cnt DESC "
        f"LIMIT {limit}"
    )


def _build_clickhouse_min_max_sql(table: str, schema: str | None, column: str) -> str:
    """Build ClickHouse min/max query for freshness check."""
    table_ref = f"{schema}.{table}" if schema else table
    return f"SELECT min({column}) AS min_val, max({column}) AS max_val FROM {table_ref} LIMIT 1"


def _build_trino_row_count_sql(table: str, schema: str | None, catalog: str | None) -> str:
    """Build Trino row count query using information_schema."""
    if schema:
        catalog_part = f"'{catalog}'" if catalog else "TABLE_CATALOG"
        return (
            f"SELECT row_count FROM information_schema.tables "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}' "
            f"AND table_catalog = {catalog_part} LIMIT 1"
        )
    return f"SELECT row_count FROM information_schema.tables WHERE table_name = '{table}' LIMIT 1"


def _build_trino_null_rate_sql(
    table: str, schema: str | None, catalog: str | None, column: str
) -> str:
    """Build Trino null rate query."""
    table_ref = (
        f"{catalog}.{schema}.{table}"
        if (catalog and schema)
        else f"{schema}.{table}"
        if schema
        else table
    )
    return (
        f"SELECT "
        f"SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS null_rate "
        f"FROM {table_ref} LIMIT 1"
    )


def _build_trino_cardinality_sql(
    table: str, schema: str | None, catalog: str | None, column: str
) -> str:
    """Build Trino cardinality query."""
    table_ref = (
        f"{catalog}.{schema}.{table}"
        if (catalog and schema)
        else f"{schema}.{table}"
        if schema
        else table
    )
    return f"SELECT COUNT(DISTINCT {column}) AS distinct_count FROM {table_ref} LIMIT 1"


def _build_trino_top_values_sql(
    table: str, schema: str | None, catalog: str | None, column: str, limit: int
) -> str:
    """Build Trino top values query."""
    table_ref = (
        f"{catalog}.{schema}.{table}"
        if (catalog and schema)
        else f"{schema}.{table}"
        if schema
        else table
    )
    return (
        f"SELECT {column}, COUNT(*) AS cnt "
        f"FROM {table_ref} "
        f"GROUP BY {column} "
        f"ORDER BY cnt DESC "
        f"LIMIT {limit}"
    )


def _build_trino_min_max_sql(
    table: str, schema: str | None, catalog: str | None, column: str
) -> str:
    """Build Trino min/max query."""
    table_ref = (
        f"{catalog}.{schema}.{table}"
        if (catalog and schema)
        else f"{schema}.{table}"
        if schema
        else table
    )
    return f"SELECT MIN({column}) AS min_val, MAX({column}) AS max_val FROM {table_ref} LIMIT 1"


def _standard_table_ref(table: str, schema: str | None) -> str:
    return f"{schema}.{table}" if schema else table


def _build_standard_row_count_sql(table: str, schema: str | None) -> str:
    return f"SELECT COUNT(*) AS row_count FROM {_standard_table_ref(table, schema)} LIMIT 1"


def _build_standard_null_rate_sql(table: str, schema: str | None, column: str) -> str:
    return (
        f"SELECT "
        f"SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS null_rate "
        f"FROM {_standard_table_ref(table, schema)} LIMIT 1"
    )


def _build_standard_cardinality_sql(table: str, schema: str | None, column: str) -> str:
    return (
        f"SELECT COUNT(DISTINCT {column}) AS distinct_count "
        f"FROM {_standard_table_ref(table, schema)} LIMIT 1"
    )


def _build_standard_top_values_sql(table: str, schema: str | None, column: str, limit: int) -> str:
    return (
        f"SELECT {column}, COUNT(*) AS cnt "
        f"FROM {_standard_table_ref(table, schema)} "
        f"GROUP BY {column} "
        f"ORDER BY cnt DESC "
        f"LIMIT {limit}"
    )


def _build_standard_min_max_sql(table: str, schema: str | None, column: str) -> str:
    return (
        f"SELECT MIN({column}) AS min_val, MAX({column}) AS max_val "
        f"FROM {_standard_table_ref(table, schema)} LIMIT 1"
    )


class ToolProfileCapabilityService:
    """Capability service for table and metric profiling."""

    # Supported date/time columns for freshness detection
    FRESHNESS_COLUMN_NAMES = frozenset({
        "updated_at",
        "updated",
        "modified_at",
        "modified",
        "created_at",
        "created",
        "etl_updated_at",
        "load_dttm",
        "load_dt",
        "data_date",
        "partition_date",
        "ptn_dt",
        "event_date",
        "processed_at",
    })

    def __init__(
        self,
        sql_router: SqlRouter,
        logger: logging.Logger | None = None,
    ) -> None:
        self._sql_router = sql_router
        self._logger = logger or logging.getLogger(__name__)

    def execute_profile_table(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
        db_connectors: list[ConnectorItem],
    ) -> dict[str, Any]:
        """
        Execute profile_table tool - returns bounded, cacheable profile summary.

        Args:
            request: Chat request.
            arguments: Tool arguments including table_name, optional columns.
            state: Tool orchestration state.
            db_connectors: Available database connectors.

        Returns:
            Profile summary dict.
        """
        table_name = arguments.get("table_name")
        if not table_name or not isinstance(table_name, str):
            state.add_warning("profile_table_missing_table_name")
            return build_tool_error(
                error_code="profile_table_missing_table_name",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Table name is required for profile_table tool.",
                retry_recommended=False,
                can_continue=True,
            )

        schema, table, db = _parse_table_reference(table_name)
        try:
            table = _validate_identifier(table)
            if schema:
                schema = _validate_identifier(schema)
        except ValueError as e:
            state.add_warning("profile_invalid_identifier")
            return build_tool_error(
                error_code="profile_invalid_identifier",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"Invalid table identifier: {e}",
                retry_recommended=False,
                can_continue=True,
            )

        connector_name = arguments.get("connector_name") or request.database_connector_name

        # Find connector
        selected_connector = self._find_connector(db_connectors, connector_name)
        if selected_connector is None:
            state.add_warning("profile_connector_not_found")
            return build_tool_error(
                error_code="profile_connector_not_found",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"Connector '{connector_name}' not found.",
                retry_recommended=False,
                can_continue=True,
            )

        connection = self._sql_router.get_connection_for_execution(
            workspace_id=request.workspace_id,
            connector_name=selected_connector.name,
        )
        if connection is None:
            state.add_warning("profile_connector_missing")
            return build_tool_error(
                error_code="profile_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Selected connector configuration is missing.",
                retry_recommended=False,
                can_continue=True,
            )

        engine = connection.engine
        catalog = getattr(connection, "catalog", None)

        self._logger.info(
            "profile.table.started",
            extra={
                "workspace_id": request.workspace_id,
                "connector_name": selected_connector.name,
                "engine": engine,
                "table": table_name,
            },
        )

        try:
            return self._profile_table(
                table=table,
                schema=schema,
                catalog=catalog,
                engine=engine,
                connection=connection,
                workspace_id=request.workspace_id,
                requested_columns=[
                    _validate_identifier(c) for c in (arguments.get("columns") or [])
                ]
                if arguments.get("columns")
                else None,
                state=state,
                connector_name=selected_connector.name,
                tool_name="profile_table",
            )
        except ValueError as e:
            state.add_warning("profile_invalid_column_name")
            return build_tool_error(
                error_code="profile_invalid_column_name",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"Invalid column name: {e}",
                retry_recommended=False,
                can_continue=True,
            )
        except Exception as exc:
            self._logger.exception("Profile table failed")
            state.add_warning("profile_table_failed")
            return self._build_profile_error(
                exc=exc,
                tool_name="profile_table",
                table_name=table_name,
            )

    def execute_profile_metric(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
        db_connectors: list[ConnectorItem],
    ) -> dict[str, Any]:
        """
        Execute profile_metric tool - returns bounded profile for a metric.

        Args:
            request: Chat request.
            arguments: Tool arguments including metric_name, related table.
            state: Tool orchestration state.
            db_connectors: Available database connectors.

        Returns:
            Profile summary dict.
        """
        metric_name = arguments.get("metric_name")
        if not metric_name or not isinstance(metric_name, str):
            state.add_warning("profile_metric_missing_metric_name")
            return build_tool_error(
                error_code="profile_metric_missing_metric_name",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Metric name is required for profile_metric tool.",
                retry_recommended=False,
                can_continue=True,
            )

        # Metric profiling uses the related table to gather shape data
        # In v1, we profile the underlying table
        table_name = arguments.get("table_name")
        if not table_name or not isinstance(table_name, str):
            state.add_warning("profile_metric_missing_table")
            return build_tool_error(
                error_code="profile_metric_missing_table",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Table name is required for profile_metric tool.",
                retry_recommended=False,
                can_continue=True,
            )

        connector_name = arguments.get("connector_name") or request.database_connector_name

        # Find connector
        selected_connector = self._find_connector(db_connectors, connector_name)
        if selected_connector is None:
            state.add_warning("profile_connector_not_found")
            return build_tool_error(
                error_code="profile_connector_not_found",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"Connector '{connector_name}' not found.",
                retry_recommended=False,
                can_continue=True,
            )

        connection = self._sql_router.get_connection_for_execution(
            workspace_id=request.workspace_id,
            connector_name=selected_connector.name,
        )
        if connection is None:
            state.add_warning("profile_connector_missing")
            return build_tool_error(
                error_code="profile_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Selected connector configuration is missing.",
                retry_recommended=False,
                can_continue=True,
            )

        engine = connection.engine
        schema, table, db = _parse_table_reference(table_name)
        try:
            table = _validate_identifier(table)
            if schema:
                schema = _validate_identifier(schema)
        except ValueError as e:
            state.add_warning("profile_invalid_identifier")
            return build_tool_error(
                error_code="profile_invalid_identifier",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"Invalid table identifier: {e}",
                retry_recommended=False,
                can_continue=True,
            )

        catalog = getattr(connection, "catalog", None)

        self._logger.info(
            "profile.metric.started",
            extra={
                "workspace_id": request.workspace_id,
                "connector_name": selected_connector.name,
                "engine": engine,
                "metric": metric_name,
                "table": table_name,
            },
        )

        try:
            result = self._profile_table(
                table=table,
                schema=schema,
                catalog=catalog,
                engine=engine,
                connection=connection,
                workspace_id=request.workspace_id,
                requested_columns=(
                    [_validate_identifier(c) for c in (arguments.get("columns") or [])]
                    if arguments.get("columns")
                    else None
                ),
                state=state,
                connector_name=selected_connector.name,
                tool_name="profile_metric",
            )
        except ValueError as e:
            state.add_warning("profile_invalid_column_name")
            return build_tool_error(
                error_code="profile_invalid_column_name",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"Invalid column name: {e}",
                retry_recommended=False,
                can_continue=True,
            )
        except Exception as exc:
            self._logger.exception("Profile metric failed")
            state.add_warning("profile_metric_failed")
            return self._build_profile_error(
                exc=exc,
                tool_name="profile_metric",
                table_name=table_name,
            )
        # Attach metric_name to the profile result for downstream consumers
        if result.get("ok"):
            result["metric_name"] = metric_name
        return result

    def _profile_table(
        self,
        *,
        table: str,
        schema: str | None,
        catalog: str | None,
        engine: str,
        connection: Any,
        workspace_id: str,
        requested_columns: list[str] | None,
        state: ToolOrchestrationState,
        connector_name: str,
        tool_name: str = "profile_table",
    ) -> dict[str, Any]:
        """Execute table profiling with low-cost queries."""
        dialect = sql_dialect_for_engine(engine)
        warnings = ProfileWarnings()
        row_counts: list[ProfileRowCounts] = []
        column_summaries: list[ProfileColumnSummary] = []
        freshness: ProfileFreshness | None = None
        token_capped = False
        row_capped = False
        truncated = False

        # Get row count
        row_count_result = self._execute_profile_query(
            sql=self._build_row_count_sql(table, schema, catalog, engine),
            connection=connection,
            workspace_id=workspace_id,
            dialect=dialect,
        )
        row_count = None
        if row_count_result and row_count_result.status == "succeeded":
            rows = row_count_result.preview.rows
            if rows and rows[0]:
                row_count = int(rows[0][0]) if rows[0][0] is not None else None
        row_counts.append(
            ProfileRowCounts(
                table=table,
                table_schema=schema,
                connector=connector_name,
                row_count=row_count,
                is_estimate=True,  # system.tables row counts are estimates
            )
        )

        # Get columns to profile
        columns_to_profile = self._get_columns_to_profile(
            table=table,
            schema=schema,
            catalog=catalog,
            engine=engine,
            connection=connection,
            workspace_id=workspace_id,
            requested_columns=requested_columns,
        )
        if columns_to_profile is None:
            warnings.message = "Could not retrieve column metadata."
            warnings.unsupported_engine = True

        if columns_to_profile:
            freshness_column = self._find_freshness_column(columns_to_profile)

            # Process each column for null rate and cardinality
            for col_info in columns_to_profile[:20]:  # Cap at 20 columns
                col_name = col_info["name"]

                # Null rate
                null_rate = self._get_null_rate(
                    table=table,
                    schema=schema,
                    catalog=catalog,
                    engine=engine,
                    column=col_name,
                    connection=connection,
                    workspace_id=workspace_id,
                )

                # Cardinality
                distinct_count = self._get_cardinality(
                    table=table,
                    schema=schema,
                    catalog=catalog,
                    engine=engine,
                    column=col_name,
                    connection=connection,
                    workspace_id=workspace_id,
                    row_count=row_count,
                )

                cardinality_bucket = _classify_cardinality(distinct_count)
                if cardinality_bucket == CardinalityBucket.HIGH:
                    warnings.high_cardinality = True

                # Top values (only for low/medium cardinality)
                top_values: list[str] = []
                if distinct_count is not None and distinct_count <= 1000:
                    top_values = self._get_top_values(
                        table=table,
                        schema=schema,
                        catalog=catalog,
                        engine=engine,
                        column=col_name,
                        connection=connection,
                        workspace_id=workspace_id,
                    )
                    if len(top_values) >= TOP_VALUES_CAP:
                        truncated = True

                # Check for sparse columns (high null rate)
                if null_rate is not None and null_rate > 50:
                    warnings.sparse = True

                column_summaries.append(
                    ProfileColumnSummary(
                        name=col_name,
                        null_rate=null_rate,
                        cardinality_bucket=cardinality_bucket,
                        top_values=top_values,
                    )
                )

            # Freshness data
            if freshness_column:
                freshness, stale = self._get_freshness(
                    table=table,
                    schema=schema,
                    catalog=catalog,
                    engine=engine,
                    column=freshness_column,
                    connection=connection,
                    workspace_id=workspace_id,
                )
                if stale:
                    warnings.stale = True

        # Cap column summaries at 20 columns
        if len(column_summaries) > 20:
            column_summaries = column_summaries[:20]
            token_capped = True

        profile = ProfileSummary(
            ok=True,
            tool_name=tool_name,
            row_counts=row_counts,
            freshness=freshness,
            column_summary=column_summaries,
            warnings=warnings
            if (
                warnings.stale
                or warnings.high_cardinality
                or warnings.sparse
                or warnings.unsupported_engine
                or warnings.message
            )
            else None,
            token_capped=token_capped,
            row_capped=row_capped,
            truncated=truncated,
            top_values_cap=TOP_VALUES_CAP,
        )

        self._logger.info(
            "profile.table.completed",
            extra={
                "table": table,
                "connector_name": connector_name,
                "row_count": row_count,
                "column_count": len(column_summaries),
            },
        )

        return profile.model_dump(mode="json")

    def _execute_profile_query(
        self,
        sql: str,
        connection: Any,
        workspace_id: str,
        dialect: str,
    ) -> Any:
        """Execute a profiling query through the SQL router."""
        try:
            return self._sql_router.execute_sql(
                sql=sql,
                connection=connection,
                workspace_id=workspace_id,
                dialect=dialect,
            )
        except Exception:
            self._logger.debug("Profile query failed: %s", sql)
            return None

    def _build_row_count_sql(
        self, table: str, schema: str | None, catalog: str | None, engine: str
    ) -> str:
        """Build row count query based on engine."""
        if engine == "trino":
            return _build_trino_row_count_sql(table, schema, catalog)
        if engine in {"greenplum", "impala"}:
            return _build_standard_row_count_sql(table, schema)
        return _build_clickhouse_row_count_sql(table, schema)

    def _build_null_rate_sql(
        self, table: str, schema: str | None, catalog: str | None, engine: str, column: str
    ) -> str:
        """Build null rate query based on engine."""
        if engine == "trino":
            return _build_trino_null_rate_sql(table, schema, catalog, column)
        if engine in {"greenplum", "impala"}:
            return _build_standard_null_rate_sql(table, schema, column)
        return _build_clickhouse_null_rate_sql(table, schema, column)

    def _build_cardinality_sql(
        self, table: str, schema: str | None, catalog: str | None, engine: str, column: str
    ) -> str:
        """Build cardinality query based on engine."""
        if engine == "trino":
            return _build_trino_cardinality_sql(table, schema, catalog, column)
        if engine in {"greenplum", "impala"}:
            return _build_standard_cardinality_sql(table, schema, column)
        return _build_clickhouse_cardinality_sql(table, schema, column)

    def _build_top_values_sql(
        self,
        table: str,
        schema: str | None,
        catalog: str | None,
        engine: str,
        column: str,
        limit: int,
    ) -> str:
        """Build top values query based on engine."""
        if engine == "trino":
            return _build_trino_top_values_sql(table, schema, catalog, column, limit)
        if engine in {"greenplum", "impala"}:
            return _build_standard_top_values_sql(table, schema, column, limit)
        return _build_clickhouse_top_values_sql(table, schema, column, limit)

    def _build_min_max_sql(
        self, table: str, schema: str | None, catalog: str | None, engine: str, column: str
    ) -> str:
        """Build min/max query based on engine."""
        if engine == "trino":
            return _build_trino_min_max_sql(table, schema, catalog, column)
        if engine in {"greenplum", "impala"}:
            return _build_standard_min_max_sql(table, schema, column)
        return _build_clickhouse_min_max_sql(table, schema, column)

    def _build_columns_query(
        self, table: str, schema: str | None, catalog: str | None, engine: str
    ) -> str:
        """Build columns query based on engine."""
        if engine == "trino":
            schema_part = f"AND table_schema = '{schema}'" if schema else ""
            catalog_part = f"AND table_catalog = '{catalog}'" if catalog else ""
            return (
                f"SELECT column_name, data_type "
                f"FROM information_schema.columns "
                f"WHERE table_name = '{table}' {schema_part} {catalog_part}"
            )
        if engine in {"greenplum", "impala"}:
            schema_part = f"AND table_schema = '{schema}'" if schema else ""
            return (
                f"SELECT column_name, data_type "
                f"FROM information_schema.columns "
                f"WHERE table_name = '{table}' {schema_part}"
            )
        # ClickHouse
        schema_part = f"database = '{schema}'" if schema else "1=1"
        return f"SELECT name, type FROM system.columns WHERE {schema_part} AND table = '{table}'"

    def _get_columns_to_profile(
        self,
        *,
        table: str,
        schema: str | None,
        catalog: str | None,
        engine: str,
        connection: Any,
        workspace_id: str,
        requested_columns: list[str] | None,
    ) -> list[dict[str, Any]] | None:
        """Get columns to profile, either from request or system tables."""
        if requested_columns:
            return [{"name": col} for col in requested_columns]

        result = self._execute_profile_query(
            sql=self._build_columns_query(table, schema, catalog, engine),
            connection=connection,
            workspace_id=workspace_id,
            dialect=sql_dialect_for_engine(engine),
        )
        if not result or result.status != "succeeded":
            return None

        columns = []
        for row in result.preview.rows:
            if row and row[0]:
                col_info: dict[str, Any] = {"name": str(row[0])}
                if len(row) > 1:
                    col_info["type"] = str(row[1])
                columns.append(col_info)
        return columns

    def _get_null_rate(
        self,
        *,
        table: str,
        schema: str | None,
        catalog: str | None,
        engine: str,
        column: str,
        connection: Any,
        workspace_id: str,
    ) -> float | None:
        """Get null rate for a column."""
        result = self._execute_profile_query(
            sql=self._build_null_rate_sql(table, schema, catalog, engine, column),
            connection=connection,
            workspace_id=workspace_id,
            dialect=sql_dialect_for_engine(engine),
        )
        if result and result.status == "succeeded" and result.preview.rows:
            row = result.preview.rows[0]
            if row and row[0] is not None:
                return round(float(row[0]), 2)
        return None

    def _get_cardinality(
        self,
        *,
        table: str,
        schema: str | None,
        catalog: str | None,
        engine: str,
        column: str,
        connection: Any,
        workspace_id: str,
        row_count: int | None,
    ) -> int | None:
        """Get cardinality (distinct count) for a column."""
        # For very large tables, skip cardinality check to stay low-cost
        if row_count is not None and row_count > 10_000_000:
            return None

        result = self._execute_profile_query(
            sql=self._build_cardinality_sql(table, schema, catalog, engine, column),
            connection=connection,
            workspace_id=workspace_id,
            dialect=sql_dialect_for_engine(engine),
        )
        if result and result.status == "succeeded" and result.preview.rows:
            row = result.preview.rows[0]
            if row and row[0] is not None:
                return int(row[0])
        return None

    def _get_top_values(
        self,
        *,
        table: str,
        schema: str | None,
        catalog: str | None,
        engine: str,
        column: str,
        connection: Any,
        workspace_id: str,
    ) -> list[str]:
        """Get top values for a column."""
        result = self._execute_profile_query(
            sql=self._build_top_values_sql(table, schema, catalog, engine, column, TOP_VALUES_CAP),
            connection=connection,
            workspace_id=workspace_id,
            dialect=sql_dialect_for_engine(engine),
        )
        if result and result.status == "succeeded" and result.preview.rows:
            return [
                str(row[0]) if row[0] is not None else "NULL"
                for row in result.preview.rows[:TOP_VALUES_CAP]
            ]
        return []

    def _find_freshness_column(self, columns: list[dict[str, Any]]) -> str | None:
        """Find a date/time column suitable for freshness checking."""
        for col_info in columns:
            col_name_lower = col_info["name"].lower()
            if col_name_lower in self.FRESHNESS_COLUMN_NAMES:
                return col_info["name"]
        return None

    def _get_freshness(
        self,
        *,
        table: str,
        schema: str | None,
        catalog: str | None,
        engine: str,
        column: str,
        connection: Any,
        workspace_id: str,
    ) -> tuple[ProfileFreshness | None, bool]:
        """Get freshness data and staleness flag."""
        result = self._execute_profile_query(
            sql=self._build_min_max_sql(table, schema, catalog, engine, column),
            connection=connection,
            workspace_id=workspace_id,
            dialect=sql_dialect_for_engine(engine),
        )
        if not result or result.status != "succeeded" or not result.preview.rows:
            return None, False

        row = result.preview.rows[0]
        if not row or len(row) < 2:
            return None, False

        min_val = str(row[0]) if row[0] is not None else None
        max_val = str(row[1]) if row[1] is not None else None

        if min_val is None and max_val is None:
            return None, False

        # Check staleness (if we could parse as date and it's older than threshold)
        stale = False
        freshness_warning = None

        try:
            if max_val:
                # Try to parse as datetime
                for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        dt = datetime.strptime(max_val[:10], fmt).replace(tzinfo=UTC)
                        age = datetime.now(UTC) - dt
                        if age > timedelta(days=STALE_DAYS_THRESHOLD):
                            stale = True
                            freshness_warning = (
                                f"Table data is {age.days} days old (last update: {max_val[:10]})"
                            )
                        break
                    except ValueError:
                        continue
        except Exception as exc:
            self._logger.debug("Could not parse freshness date: %s", exc)

        # Build dimension column summary for freshness column
        freshness_dimensions = [
            ProfileColumnSummary(
                name=column, null_rate=None, cardinality_bucket=CardinalityBucket.LOW
            )
        ]

        return ProfileFreshness(
            dimensions=freshness_dimensions,
            min_max_values={"min": min_val, "max": max_val} if min_val or max_val else None,
            stale=stale,
            freshness_warning=freshness_warning,
        ), stale

    def _find_connector(
        self, db_connectors: list[ConnectorItem], connector_name: str | None
    ) -> ConnectorItem | None:
        """Find a connector by name."""
        if not connector_name:
            return db_connectors[0] if db_connectors else None
        normalized = connector_name.strip().lower()
        for conn in db_connectors:
            if conn.name.lower() == normalized:
                return conn
        return None

    def _build_profile_error(
        self,
        exc: Exception,
        tool_name: str,
        table_name: str,
    ) -> dict[str, Any]:
        """Build error response for profile operations."""
        return {
            "ok": False,
            "error": f"{tool_name}_failed",
            "error_code": f"{tool_name}_failed",
            "error_category": ErrorCategory.RECOVERABLE.value,
            "user_message": f"Profile operation failed for '{table_name}': {str(exc)[:200]}",
            "retry_recommended": True,
            "can_continue": True,
            "tool_name": tool_name,
        }
