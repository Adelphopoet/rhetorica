"""Compatibility facade over focused runtime tool-execution services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.domain.chat.error_contract import (
    build_tool_error,
    classify_exception,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_context_hydration_helpers import (
    ToolExecutionContextHydrationHelpers,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_context_support import (
    ToolExecutionContextSupport,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_context_service import (
    ToolExecutionMetadataContextService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_connector_policy import (
    ToolExecutionRuntimeConnectorPolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_sql_policy import (
    ToolExecutionRuntimeSqlPolicy,
)
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SqlPreflightResult,
    SqlStatementInspection,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolExecutionRuntimeSupport(ToolExecutionContextSupport):
    """Shared runtime support for ToolExecutor and SQL/metadata services."""

    _connector_scope_policy = ToolExecutionConnectorScopePolicy()
    _metadata_context_service = ToolExecutionMetadataContextService(
        connector_scope_policy=_connector_scope_policy,
    )
    _context_hydration_helpers = ToolExecutionContextHydrationHelpers()
    _runtime_sql_policy = ToolExecutionRuntimeSqlPolicy(
        connector_scope_policy=_connector_scope_policy,
    )
    _runtime_connector_policy = ToolExecutionRuntimeConnectorPolicy(
        connector_scope_policy=_connector_scope_policy,
    )

    def _resolve_metadata_connection(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        warnings: list[str],
        db_connectors: list[ConnectorItem] | None = None,
    ):
        return self._metadata_runtime_service.resolve_metadata_connection(
            request=request,
            arguments=arguments,
            warnings=warnings,
            db_connectors=db_connectors or [],
        )

    def _resolve_effective_metadata_connection(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        warnings: list[str],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState | None = None,
    ) -> Any | None:
        return self._metadata_runtime_service.resolve_effective_metadata_connection(
            request=request,
            arguments=arguments,
            warnings=warnings,
            db_connectors=db_connectors,
            state=state,
        )

    @classmethod
    def _attempt_sql_identifier_repair(
        cls,
        *,
        sql: str,
        dialect: str,
        preflight_result: SqlPreflightResult,
    ) -> str | None:
        return cls._runtime_sql_policy.attempt_sql_identifier_repair(
            sql=sql,
            dialect=dialect,
            preflight_result=preflight_result,
        )

    def _attempt_metadata_context_repair(
        self,
        *,
        request: ChatAskRequest,
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
        connection: Any,
        missing_tables: list[str],
    ) -> bool:
        return self._metadata_runtime_service.attempt_metadata_context_repair(
            request=request,
            state=state,
            selected_connector=selected_connector,
            connection=connection,
            missing_tables=missing_tables,
        )

    @classmethod
    def _preflight_metadata_context(
        cls,
        *,
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
    ) -> tuple[list[str], dict[str, set[str]]]:
        return cls._runtime_sql_policy.preflight_metadata_context(
            state=state,
            selected_connector=selected_connector,
        )

    def _hydrate_preflight_table_schemas(
        self,
        *,
        request: ChatAskRequest,
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
        connection: Any,
        sql: str,
        dialect: str,
    ) -> SqlStatementInspection:
        outcome = self._sql_schema_hydration_service.hydrate_for_preflight(
            request=request,
            state=state,
            selected_connector=selected_connector,
            connection=connection,
            sql=sql,
            dialect=dialect,
        )
        if outcome.context_repaired:
            state.add_warning("sql_preflight_context_repaired")
        return outcome.inspection

    @staticmethod
    def _requested_connector_name(arguments: dict[str, Any]) -> str | None:
        return ToolExecutionRuntimeSupport._runtime_sql_policy.requested_connector_name(
            arguments
        )

    @classmethod
    def _preflight_connector_name(
        cls,
        *,
        arguments: dict[str, Any],
        selected_connector: ConnectorItem,
        connection: Any,
    ) -> str | None:
        return cls._runtime_sql_policy.preflight_connector_name(
            arguments=arguments,
            selected_connector=selected_connector,
            connection=connection,
        )

    @classmethod
    def _normalize_sql_identifiers(
        cls,
        *,
        sql: str,
        requested_connector_name: str | None,
        selected_connector: ConnectorItem,
        connection: Any,
    ) -> str:
        return cls._runtime_sql_policy.normalize_sql_identifiers(
            sql=sql,
            requested_connector_name=requested_connector_name,
            selected_connector=selected_connector,
            connection=connection,
        )

    @classmethod
    def _normalize_sql_table_qualifiers(
        cls,
        *,
        sql: str,
        dialect: str,
    ) -> str:
        return cls._runtime_sql_policy.normalize_sql_table_qualifiers(
            sql=sql,
            dialect=dialect,
        )

    @staticmethod
    def _rewrite_trino_show_create_table(sql: str) -> str:
        return ToolExecutionRuntimeSupport._runtime_sql_policy.rewrite_trino_show_create_table(
            sql
        )

    @staticmethod
    def _metadata_service_name(connection: Any) -> str | None:
        return ToolExecutionRuntimeSupport._runtime_sql_policy.metadata_service_name(
            connection
        )

    @staticmethod
    def _metadata_mapping_value(connection: Any, key: str) -> str | None:
        return ToolExecutionRuntimeSupport._runtime_sql_policy.metadata_mapping_value(
            connection,
            key,
        )

    @classmethod
    def _effective_metadata_connection(
        cls,
        *,
        metadata_connection: Any,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState | None,
    ) -> Any:
        return cls._runtime_sql_policy.effective_metadata_connection(
            metadata_connection=metadata_connection,
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def _select_runtime_lookup_connector(
        self,
        *,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        metadata_connection: Any | None,
    ) -> ConnectorItem | None:
        return self._runtime_sql_policy.select_runtime_lookup_connector(
            request=request,
            db_connectors=db_connectors,
            metadata_connection=metadata_connection,
            sql_router=self._sql_router,
        )

    @staticmethod
    def _runtime_lookup_target(search: str) -> tuple[str | None, str | None]:
        return ToolExecutionRuntimeSupport._runtime_sql_policy.runtime_lookup_target(search)

    @staticmethod
    def _runtime_tables_lookup_sql(
        *,
        connection: Any,
        leaf_name: str,
        database_hint: str | None,
        limit: int,
        exact: bool = True,
    ) -> str:
        return ToolExecutionRuntimeSupport._runtime_sql_policy.runtime_tables_lookup_sql(
            connection=connection,
            leaf_name=leaf_name,
            database_hint=database_hint,
            limit=limit,
            exact=exact,
        )

    @staticmethod
    def _runtime_columns_lookup_sql(
        *,
        connection: Any,
        schema_name: str,
        table_name: str,
    ) -> str:
        return ToolExecutionRuntimeSupport._runtime_sql_policy.runtime_columns_lookup_sql(
            connection=connection,
            schema_name=schema_name,
            table_name=table_name,
        )

    @classmethod
    def _runtime_table_fqn(
        cls,
        *,
        connection: Any,
        schema_name: str,
        table_name: str,
    ) -> str:
        return cls._runtime_sql_policy.runtime_table_fqn(
            connection=connection,
            schema_name=schema_name,
            table_name=table_name,
        )

    @classmethod
    def _preferred_metadata_scope_connector(
        cls,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState | None,
    ) -> ConnectorItem | None:
        return cls._runtime_sql_policy.preferred_metadata_scope_connector(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    @classmethod
    def _has_multiple_connector_scopes(
        cls,
        db_connectors: list[ConnectorItem],
    ) -> bool:
        return cls._runtime_sql_policy.has_multiple_connector_scopes(db_connectors)

    @staticmethod
    def _preflight_error_payload(
        *,
        preflight_result: SqlPreflightResult,
        generated_sql: str,
        selected_connector_name: str,
        error_code: str,
        user_message: str,
    ) -> dict[str, Any]:
        return ToolExecutionRuntimeSupport._runtime_sql_policy.preflight_error_payload(
            preflight_result=preflight_result,
            generated_sql=generated_sql,
            selected_connector_name=selected_connector_name,
            error_code=error_code,
            user_message=user_message,
        )

    @staticmethod
    def _can_bypass_preflight_failure(
        *,
        preflight_result: SqlPreflightResult,
        state: ToolOrchestrationState,
        inspection: SqlStatementInspection,
    ) -> bool:
        return ToolExecutionRuntimeSupport._runtime_sql_policy.can_bypass_preflight_failure(
            preflight_result=preflight_result,
            state=state,
            inspection=inspection,
        )

    @staticmethod
    def _tool_error_from_exception(
        exc: Exception,
        *,
        default_error_code: str,
        can_continue: bool,
    ) -> dict[str, Any]:
        category, classified_code, user_message, retry_recommended = classify_exception(
            exc
        )
        code = classified_code or default_error_code
        return build_tool_error(
            error_code=code,
            error_category=category,
            user_message=user_message,
            retry_recommended=retry_recommended,
            can_continue=can_continue,
        )

    def _hydrate_context_from_execution(
        self,
        *,
        state: ToolOrchestrationState,
        sql: str,
        execution: ExecuteSqlResponse,
    ) -> None:
        self._context_hydration_helpers.hydrate_context_from_execution(
            state=state,
            sql=sql,
            execution=execution,
            merge_context_tables=self._merge_context_tables,
            set_table_columns=self._set_table_columns,
            set_table_schema_snapshot=self._metadata_state_context_service.set_table_schema_snapshot,
        )

    @classmethod
    def _prefer_context_connector(
        cls,
        *,
        request: ChatAskRequest,
        requested_connector_name: str | None,
        sql: str,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem | None,
    ) -> ConnectorItem | None:
        return cls._runtime_connector_policy.prefer_context_connector(
            request=request,
            requested_connector_name=requested_connector_name,
            sql=sql,
            db_connectors=db_connectors,
            state=state,
            selected_connector=selected_connector,
        )

    def _unavailable_context_connector_error(
        self,
        *,
        request: ChatAskRequest,
        sql: str,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
    ) -> dict[str, Any] | None:
        return self._runtime_connector_policy.unavailable_context_connector_error(
            request=request,
            sql=sql,
            db_connectors=db_connectors,
            state=state,
            selected_connector=selected_connector,
        )

    @classmethod
    def _infer_connector_from_context_tables(
        cls,
        *,
        sql: str,
        db_connectors: list[ConnectorItem],
        context_tables: list[str],
    ) -> ConnectorItem | None:
        return cls._runtime_connector_policy.infer_connector_from_context_tables(
            sql=sql,
            db_connectors=db_connectors,
            context_tables=context_tables,
        )

    @classmethod
    def _matched_context_table_mappings(
        cls,
        *,
        sql: str,
        context_tables: list[str],
    ) -> list[dict[str, str]]:
        return cls._runtime_connector_policy.matched_context_table_mappings(
            sql=sql,
            context_tables=context_tables,
        )

    @staticmethod
    def _normalize_identifier_for_match(value: str) -> str:
        return ToolExecutionRuntimeSupport._runtime_connector_policy.normalize_identifier_for_match(
            value
        )

    @staticmethod
    def _table_match_patterns(mapping: dict[str, str]) -> tuple[str, ...]:
        return ToolExecutionRuntimeSupport._runtime_connector_policy.table_match_patterns(
            mapping
        )

    @staticmethod
    def _contains_table_identifier(sql: str, identifier: str) -> bool:
        return ToolExecutionRuntimeSupport._runtime_connector_policy.contains_table_identifier(
            sql,
            identifier,
        )

    @classmethod
    def _metadata_tool_argument_connector_name(
        cls,
        *,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> str | None:
        return cls._runtime_sql_policy.metadata_tool_argument_connector_name(
            arguments=arguments,
            db_connectors=db_connectors,
        )

    @classmethod
    def _metadata_tool_scope_connector_name(
        cls,
        *,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> str | None:
        return cls._runtime_sql_policy.metadata_tool_scope_connector_name(
            arguments=arguments,
            db_connectors=db_connectors,
        )

    @classmethod
    def _metadata_tool_connector_name(
        cls,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> str | None:
        return cls._runtime_sql_policy.metadata_tool_connector_name(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
        )

    @staticmethod
    def _extract_table_names_from_execution(
        execution: ExecuteSqlResponse,
    ) -> list[str]:
        return ToolExecutionRuntimeSupport._context_hydration_helpers.extract_table_names_from_execution(
            execution
        )
