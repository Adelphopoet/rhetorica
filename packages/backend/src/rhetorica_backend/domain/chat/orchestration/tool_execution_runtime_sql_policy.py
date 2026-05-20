"""Runtime SQL normalization and connector-scope policy for tool execution."""

from __future__ import annotations

import re
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import sql_dialect_for_engine
from rhetorica_backend.domain.chat.error_contract import ErrorCategory, build_tool_error
from rhetorica_backend.domain.chat.metadata.connection_resolver import (
    clone_metadata_connection_with_scope,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
)
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SqlPreflightResult,
    SqlStatementInspection,
)
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


class ToolExecutionRuntimeSqlPolicy:
    """Owns SQL normalization, metadata scope mapping, and preflight helpers."""

    def __init__(
        self,
        *,
        connector_scope_policy: ToolExecutionConnectorScopePolicy,
    ) -> None:
        self._connector_scope_policy = connector_scope_policy

    @classmethod
    def attempt_sql_identifier_repair(
        cls,
        *,
        sql: str,
        dialect: str,
        preflight_result: SqlPreflightResult,
    ) -> str | None:
        replacements: dict[str, str] = {}
        for error in preflight_result.errors:
            if error.code != "missing_column":
                continue
            if not isinstance(error.identifier, str) or not error.identifier.strip():
                continue
            if len(error.suggestions) != 1:
                return None
            source = error.identifier.strip().lower()
            target = error.suggestions[0].strip()
            if not target or target.lower() == source:
                continue
            replacements[source] = target
        if not replacements:
            return None

        try:
            parsed = parse_one(sql, read=dialect)
        except ParseError:
            return None

        changed = False

        def _replace(node: exp.Expression) -> exp.Expression:
            nonlocal changed
            if not isinstance(node, exp.Column):
                return node
            column_name = node.name
            if not isinstance(column_name, str) or not column_name.strip():
                return node
            replacement = replacements.get(column_name.strip().lower())
            if not replacement or replacement == column_name:
                return node
            changed = True
            return exp.column(
                replacement,
                table=node.table or None,
                db=node.db or None,
                catalog=node.catalog or None,
                quoted=node.args.get("quoted"),
            )

        repaired = parsed.transform(_replace)
        if not changed:
            return None
        return repaired.sql(dialect=dialect)

    def preflight_metadata_context(
        self,
        *,
        state: Any,
        selected_connector: ConnectorItem,
    ) -> tuple[list[str], dict[str, set[str]]]:
        scoped_tables = self._connector_scope_policy.context_tables_for_connector_scope(
            context_tables=state.context_tables,
            selected_connector=selected_connector,
        )
        scoped_columns = self._connector_scope_policy.metadata_columns_for_connector_scope(
            metadata_table_columns=state.metadata_table_columns,
            selected_connector=selected_connector,
        )
        if scoped_tables or scoped_columns:
            return scoped_tables, scoped_columns
        return state.context_tables, state.metadata_table_columns

    @staticmethod
    def requested_connector_name(arguments: dict[str, Any]) -> str | None:
        connector_name = arguments.get("connector_name")
        if isinstance(connector_name, str) and connector_name.strip():
            return connector_name.strip()
        return None

    def preflight_connector_name(
        self,
        *,
        arguments: dict[str, Any],
        selected_connector: ConnectorItem,
        connection: Any,
    ) -> str | None:
        requested = self.requested_connector_name(arguments)
        if requested is None:
            return None
        metadata_service = self.metadata_service_name(connection)
        normalized_requested = requested.strip().lower()
        if normalized_requested == selected_connector.name.strip().lower():
            return selected_connector.name
        if metadata_service and normalized_requested == metadata_service.strip().lower():
            return selected_connector.name
        return requested

    def normalize_sql_identifiers(
        self,
        *,
        sql: str,
        requested_connector_name: str | None,
        selected_connector: ConnectorItem,
        connection: Any,
    ) -> str:
        normalized_sql = sql
        metadata_database = self.metadata_mapping_value(connection, key="metadata_database")
        connection_catalog = getattr(connection, "catalog", None)
        prefixes = [
            requested_connector_name,
            selected_connector.name,
            self.metadata_service_name(connection),
        ]
        if not (
            isinstance(metadata_database, str)
            and metadata_database.strip()
            and isinstance(connection_catalog, str)
            and connection_catalog.strip()
            and metadata_database.strip().lower() == connection_catalog.strip().lower()
        ):
            prefixes.append(metadata_database)
        for prefix in prefixes:
            if not isinstance(prefix, str) or not prefix.strip():
                continue
            normalized_sql = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(prefix.strip())}\.",
                "",
                normalized_sql,
            )
        normalized_sql = self.normalize_sql_table_qualifiers(
            sql=normalized_sql,
            dialect=sql_dialect_for_engine(str(getattr(connection, "engine", "") or "")),
        )
        if getattr(connection, "engine", None) == "trino":
            normalized_sql = self.rewrite_trino_show_create_table(normalized_sql)
        return normalized_sql.strip()

    @classmethod
    def normalize_sql_table_qualifiers(
        cls,
        *,
        sql: str,
        dialect: str,
    ) -> str:
        if dialect not in {"clickhouse", "trino", "postgres", "hive"}:
            return sql
        try:
            parsed = parse_one(sql, read=dialect)
        except ParseError:
            return sql
        changed = False

        def _rewrite(node: exp.Expression) -> exp.Expression:
            nonlocal changed
            if not isinstance(node, exp.Table):
                return node
            parts = [re.sub(r'["`]', "", part.sql(dialect=dialect)).strip() for part in node.parts]
            parts = [part for part in parts if part]
            if dialect == "trino":
                if len(parts) <= 3:
                    return node
                target_parts = parts[-3:]
            elif dialect in {"postgres", "hive"}:
                if len(parts) <= 2:
                    return node
                target_parts = parts[-2:]
            else:
                if len(parts) <= 3:
                    return node
                target_parts = parts[-2:]
            updated = node.copy()
            updated.set("catalog", None)
            updated.set("db", None)
            updated.set("this", exp.to_identifier(target_parts[-1]))
            if len(target_parts) >= 2:
                updated.set("db", exp.to_identifier(target_parts[-2]))
            if len(target_parts) >= 3:
                updated.set("catalog", exp.to_identifier(target_parts[-3]))
            changed = True
            return updated

        repaired = parsed.transform(_rewrite)
        if not changed:
            return sql
        return repaired.sql(dialect=dialect)

    @staticmethod
    def rewrite_trino_show_create_table(sql: str) -> str:
        match = re.match(
            r"^\s*show\s+create\s+table\s+(.+?)\s*;?\s*$",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            return sql
        target = match.group(1).strip()
        if not target:
            return sql
        return f"SHOW COLUMNS FROM {target}"

    @staticmethod
    def metadata_service_name(connection: Any) -> str | None:
        extra_settings = getattr(connection, "extra_settings", None)
        if not isinstance(extra_settings, dict):
            return None
        metadata_service = extra_settings.get("metadata_service")
        if isinstance(metadata_service, str) and metadata_service.strip():
            return metadata_service.strip()
        return None

    @staticmethod
    def metadata_mapping_value(connection: Any, key: str) -> str | None:
        extra_settings = getattr(connection, "extra_settings", None)
        if not isinstance(extra_settings, dict):
            return None
        value = extra_settings.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def effective_metadata_connection(
        self,
        *,
        metadata_connection: Any,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: Any | None,
    ) -> Any:
        explicit_metadata_connector = self.metadata_tool_argument_connector_name(
            arguments=arguments,
            db_connectors=db_connectors,
        ) or (
            isinstance(request.metadata_connector_name, str)
            and request.metadata_connector_name.strip()
        )
        preferred_connector = self.preferred_metadata_scope_connector(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )
        if preferred_connector is not None:
            return clone_metadata_connection_with_scope(
                metadata_connection,
                service_name=self._connector_scope_policy.connector_mapping_value(
                    preferred_connector,
                    "metadata_service",
                ),
                database_name=self._connector_scope_policy.connector_mapping_value(
                    preferred_connector,
                    "metadata_database",
                ),
                schema_name=self._connector_scope_policy.connector_mapping_value(
                    preferred_connector,
                    "metadata_schema",
                ),
            )
        if explicit_metadata_connector or not self.has_multiple_connector_scopes(db_connectors):
            return metadata_connection
        return clone_metadata_connection_with_scope(
            metadata_connection,
            service_name=None,
            database_name=None,
            schema_name=None,
        )

    def select_runtime_lookup_connector(
        self,
        *,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        metadata_connection: Any | None,
        sql_router: Any,
    ) -> ConnectorItem | None:
        if not db_connectors:
            return None
        if metadata_connection is not None:
            selected = sql_router.select_connector(
                request=request,
                llm_payload={
                    "target": {
                        "service": getattr(metadata_connection, "service_name", None),
                        "database": getattr(metadata_connection, "database_name", None),
                        "schema": getattr(metadata_connection, "schema_name", None),
                    }
                },
                db_connectors=db_connectors,
            )
            if selected is not None:
                return selected
        return db_connectors[0] if len(db_connectors) == 1 else None

    @staticmethod
    def runtime_lookup_target(search: str) -> tuple[str | None, str | None]:
        clean = search.strip().replace("`", "").replace('"', "")
        parts = [part.strip().lower() for part in clean.split(".") if part.strip()]
        if not parts:
            return None, None
        leaf_name = parts[-1]
        if not re.fullmatch(r"[a-z0-9_]+", leaf_name):
            return None, None
        database_hint = None
        if len(parts) >= 2 and re.fullmatch(r"[a-z0-9_]+", parts[-2]):
            database_hint = parts[-2]
        return leaf_name, database_hint

    @staticmethod
    def runtime_tables_lookup_sql(
        *,
        connection: Any,
        leaf_name: str,
        database_hint: str | None,
        limit: int,
        exact: bool = True,
    ) -> str:
        engine = getattr(connection, "engine", None)
        safe_limit = max(1, min(limit, 20))
        if engine in {"trino", "greenplum", "impala"}:
            where_clauses = (
                [f"table_name = '{leaf_name}'"]
                if exact
                else [f"lower(table_name) LIKE '%{leaf_name}%'"]
            )
            if database_hint:
                where_clauses.append(f"table_schema = '{database_hint}'")
            where_clauses.append("table_type = 'BASE TABLE'")
            where_sql = " AND ".join(where_clauses)
            return (
                "SELECT table_schema, table_name "
                "FROM information_schema.tables "
                f"WHERE {where_sql} "
                f"ORDER BY table_schema, table_name LIMIT {safe_limit}"
            )
        where_clauses = (
            [f"name = '{leaf_name}'"] if exact else [f"lower(name) LIKE '%{leaf_name}%'"]
        )
        if database_hint:
            where_clauses.append(f"database = '{database_hint}'")
        where_sql = " AND ".join(where_clauses)
        return (
            "SELECT database, name FROM system.tables "
            f"WHERE {where_sql} ORDER BY database, name LIMIT {safe_limit}"
        )

    @staticmethod
    def runtime_columns_lookup_sql(
        *,
        connection: Any,
        schema_name: str,
        table_name: str,
    ) -> str:
        if getattr(connection, "engine", None) in {"trino", "greenplum", "impala"}:
            return (
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                f"WHERE table_schema = '{schema_name}' AND table_name = '{table_name}' "
                "ORDER BY ordinal_position LIMIT 200"
            )
        return (
            "SELECT name, type FROM system.columns "
            f"WHERE database = '{schema_name}' AND table = '{table_name}' "
            "ORDER BY position LIMIT 200"
        )

    def runtime_table_fqn(
        self,
        *,
        connection: Any,
        schema_name: str,
        table_name: str,
    ) -> str:
        metadata_service = self.metadata_service_name(connection)
        metadata_database = self.metadata_mapping_value(
            connection=connection,
            key="metadata_database",
        )
        if metadata_service and metadata_database:
            return f"{metadata_service}.{metadata_database}.{schema_name}.{table_name}"
        if metadata_service:
            return f"{metadata_service}.{schema_name}.{table_name}"
        return f"{schema_name}.{table_name}"

    def preferred_metadata_scope_connector(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: Any | None,
    ) -> ConnectorItem | None:
        preferred_names: list[str] = []
        tool_connector_name = self.metadata_tool_scope_connector_name(
            arguments=arguments,
            db_connectors=db_connectors,
        )
        if tool_connector_name is not None:
            preferred_names.append(tool_connector_name)
        if (
            isinstance(request.database_connector_name, str)
            and request.database_connector_name.strip()
        ):
            preferred_names.append(request.database_connector_name.strip())
        if state is not None:
            if (
                isinstance(state.selected_database_connector, str)
                and state.selected_database_connector.strip()
            ):
                preferred_names.append(state.selected_database_connector.strip())
            execution_zone = state.execution_zone
            if (
                execution_zone is not None
                and isinstance(execution_zone.connector_name, str)
                and execution_zone.connector_name.strip()
            ):
                preferred_names.append(execution_zone.connector_name.strip())
        for connector_name in preferred_names:
            connector = self._connector_scope_policy.find_connector_by_name(
                db_connectors=db_connectors,
                connector_name=connector_name,
            )
            if connector is not None:
                return connector
        if len(db_connectors) == 1:
            return db_connectors[0]
        return None

    def has_multiple_connector_scopes(
        self,
        db_connectors: list[ConnectorItem],
    ) -> bool:
        scopes: set[tuple[str, str, str]] = set()
        for connector in db_connectors:
            service = self._connector_scope_policy.connector_mapping_value(
                connector,
                "metadata_service",
            )
            database = self._connector_scope_policy.connector_mapping_value(
                connector,
                "metadata_database",
            )
            schema = (
                self._connector_scope_policy.connector_mapping_value(
                    connector,
                    "metadata_schema",
                )
                or ""
            )
            if service is None or database is None:
                continue
            scopes.add((service.lower(), database.lower(), schema.lower()))
            if len(scopes) > 1:
                return True
        return False

    @staticmethod
    def preflight_error_payload(
        *,
        preflight_result: SqlPreflightResult,
        generated_sql: str,
        selected_connector_name: str,
        error_code: str,
        user_message: str,
    ) -> dict[str, Any]:
        payload = build_tool_error(
            error_code=error_code,
            error_category=ErrorCategory.RECOVERABLE,
            user_message=user_message,
            retry_recommended=True,
            can_continue=True,
        )
        payload.update({
            "generated_sql": generated_sql,
            "selected_database_connector": selected_connector_name,
            "preflight_status": preflight_result.status,
            "preflight_errors": [item.model_dump(mode="json") for item in preflight_result.errors],
            "referenced_tables": preflight_result.referenced_tables,
            "referenced_columns": preflight_result.referenced_columns,
        })
        return payload

    @staticmethod
    def can_bypass_preflight_failure(
        *,
        preflight_result: SqlPreflightResult,
        state: Any,
        inspection: SqlStatementInspection,
    ) -> bool:
        _ = state, inspection
        if not preflight_result.errors:
            return False
        if preflight_result.status == "fail":
            return all(
                item.code in {"missing_table", "missing_column"} for item in preflight_result.errors
            )
        if preflight_result.status == "uncertain":
            return bool(inspection.parsed and inspection.referenced_tables) and all(
                item.code == "metadata_context_missing" for item in preflight_result.errors
            )
        return False

    def metadata_tool_argument_connector_name(
        self,
        *,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> str | None:
        connector_name = self.requested_connector_name(arguments)
        if connector_name is None:
            return None
        if (
            self._connector_scope_policy.find_connector_by_name(
                db_connectors=db_connectors,
                connector_name=connector_name,
            )
            is not None
        ):
            return None
        return connector_name

    def metadata_tool_scope_connector_name(
        self,
        *,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> str | None:
        connector_name = self.requested_connector_name(arguments)
        if connector_name is None:
            return None
        connector = self._connector_scope_policy.find_connector_by_name(
            db_connectors=db_connectors,
            connector_name=connector_name,
        )
        return connector.name if connector is not None else None

    def metadata_tool_connector_name(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> str | None:
        connector_name = self.metadata_tool_argument_connector_name(
            arguments=arguments,
            db_connectors=db_connectors,
        )
        if connector_name is not None:
            return connector_name
        if (
            isinstance(request.metadata_connector_name, str)
            and request.metadata_connector_name.strip()
        ):
            return request.metadata_connector_name.strip()
        return None
