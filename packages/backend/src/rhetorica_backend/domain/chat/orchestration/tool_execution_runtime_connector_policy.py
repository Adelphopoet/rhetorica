"""Connector-selection policy for runtime SQL execution."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.error_contract import ErrorCategory, build_tool_error
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolExecutionRuntimeConnectorPolicy:
    """Owns connector inference and context-scope execution guards."""

    def __init__(
        self,
        *,
        connector_scope_policy: ToolExecutionConnectorScopePolicy,
    ) -> None:
        self._connector_scope_policy = connector_scope_policy

    def prefer_context_connector(
        self,
        *,
        request: ChatAskRequest,
        requested_connector_name: str | None,
        sql: str,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem | None,
    ) -> ConnectorItem | None:
        if request.database_connector_name or requested_connector_name:
            return selected_connector
        if (
            state.execution_zone is not None
            and isinstance(state.execution_zone.connector_name, str)
            and state.execution_zone.connector_name.strip()
        ):
            zone_connector = self._connector_scope_policy.find_connector_by_name(
                db_connectors=db_connectors,
                connector_name=state.execution_zone.connector_name,
            )
            if zone_connector is not None:
                if selected_connector is None or zone_connector.name != selected_connector.name:
                    state.warnings.append("sql_connector_overridden_from_execution_zone")
                return zone_connector
        if not state.context_tables:
            return selected_connector

        inferred = self.infer_connector_from_context_tables(
            sql=sql,
            db_connectors=db_connectors,
            context_tables=state.context_tables,
        )
        if inferred is None:
            return selected_connector
        if selected_connector is None or inferred.name != selected_connector.name:
            state.warnings.append("sql_connector_overridden_from_metadata_context")
            return inferred
        return selected_connector

    def unavailable_context_connector_error(
        self,
        *,
        request: ChatAskRequest,
        sql: str,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
    ) -> dict[str, Any] | None:
        matched_contexts = self.matched_context_table_mappings(
            sql=sql,
            context_tables=state.context_tables,
        )
        if not matched_contexts:
            return None

        available_connectors: list[ConnectorItem] = []
        unavailable_mappings: list[dict[str, str]] = []
        seen_unavailable: set[str] = set()
        normalized_sql = self.normalize_identifier_for_match(sql)
        selected_engine = getattr(selected_connector.connection, "engine", None)
        for mapping in matched_contexts:
            connector = self._connector_scope_policy.find_connector_by_mapping(
                db_connectors=db_connectors,
                service=mapping["service"],
                database=mapping["database"],
                schema=mapping["schema"],
            )
            if connector is not None:
                available_connectors.append(connector)
                continue
            explicit_service_match = (
                f"{mapping['service'].lower()}.{mapping['database'].lower()}."
                f"{mapping['schema'].lower()}.{mapping['table'].lower()}"
            ) in normalized_sql
            if selected_engine == "trino" and not explicit_service_match:
                continue
            unavailable_key = (
                f"{mapping['service']}.{mapping['database']}.{mapping['schema']}.{mapping['table']}"
            )
            if unavailable_key in seen_unavailable:
                continue
            seen_unavailable.add(unavailable_key)
            unavailable_mappings.append(mapping)

        if unavailable_mappings and not available_connectors:
            _ = request
            state.unavailable_context_connector_fail += 1
            state.warnings.append("sql_execution_blocked_unavailable_context_connector")
            available_names = ", ".join(connector.name for connector in db_connectors) or "none"
            unavailable_names = ", ".join(
                f"{item['service']}.{item['database']}.{item['schema']}.{item['table']}"
                for item in unavailable_mappings[:3]
            )
            payload = build_tool_error(
                error_code="database_connector_unavailable_for_context_table",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=(
                    "The referenced table is known only in metadata sources without a configured "
                    f"database connector: {unavailable_names}. Use one of the connected database "
                    f"connectors instead: {available_names}."
                ),
                retry_recommended=False,
                can_continue=True,
            )
            payload["unavailable_context_tables"] = unavailable_names.split(", ")
            payload["available_database_connectors"] = [
                connector.name for connector in db_connectors
            ]
            payload["selected_database_connector"] = selected_connector.name
            payload["generated_sql"] = sql
            return payload
        return None

    def infer_connector_from_context_tables(
        self,
        *,
        sql: str,
        db_connectors: list[ConnectorItem],
        context_tables: list[str],
    ) -> ConnectorItem | None:
        matched_connectors: list[ConnectorItem] = []
        seen_names: set[str] = set()
        for mapping in self.matched_context_table_mappings(
            sql=sql,
            context_tables=context_tables,
        ):
            connector = self._connector_scope_policy.find_connector_by_mapping(
                db_connectors=db_connectors,
                service=mapping["service"],
                database=mapping["database"],
                schema=mapping["schema"],
            )
            if connector is None or connector.name in seen_names:
                continue
            seen_names.add(connector.name)
            matched_connectors.append(connector)

        if len(matched_connectors) == 1:
            return matched_connectors[0]
        return None

    def matched_context_table_mappings(
        self,
        *,
        sql: str,
        context_tables: list[str],
    ) -> list[dict[str, str]]:
        normalized_sql = self.normalize_identifier_for_match(sql)
        matched: list[dict[str, str]] = []
        seen_keys: set[str] = set()
        for table_fqn in context_tables:
            mapping = self._connector_scope_policy.table_mapping_from_fqn(table_fqn)
            if mapping is None:
                continue
            table_patterns = self.table_match_patterns(mapping)
            if not any(
                self.contains_table_identifier(normalized_sql, pattern)
                for pattern in table_patterns
            ):
                continue
            key = (
                f"{mapping['service']}.{mapping['database']}.{mapping['schema']}.{mapping['table']}"
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            matched.append(mapping)
        return matched

    @staticmethod
    def normalize_identifier_for_match(value: str) -> str:
        return re.sub(r'["`]', "", value).strip().lower()

    @staticmethod
    def table_match_patterns(mapping: dict[str, str]) -> tuple[str, ...]:
        database = mapping["database"].lower()
        schema = mapping["schema"].lower()
        table = mapping["table"].lower()
        service = mapping["service"].lower()
        return (
            f"{database}.{schema}.{table}",
            f"{schema}.{table}",
            f" {table} ",
            f" {schema}.{table} ",
            f"{service}.{database}.{schema}.{table}",
        )

    @staticmethod
    def contains_table_identifier(sql: str, identifier: str) -> bool:
        escaped = re.escape(identifier.strip().lower())
        if not escaped:
            return False
        pattern = rf"(?<![a-z0-9_.]){escaped}(?![a-z0-9_])"
        return re.search(pattern, sql) is not None
