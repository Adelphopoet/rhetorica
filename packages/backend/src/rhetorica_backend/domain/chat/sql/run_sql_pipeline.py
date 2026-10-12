"""Dedicated pipeline for SQL preflight and execution orchestration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import sql_dialect_for_engine
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
)
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
    SqlPreflightResult,
    SqlStatementInspection,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.chat.sql_prompt_window import (
    normalize_sql_execution_row_limit,
    normalize_sql_prompt_row_limit,
)
from rhetorica_backend.domain.metadata.metric_registry_service import (
    MetricRegistryService,
)
from rhetorica_backend.infra.logging import sanitize_for_logging

if TYPE_CHECKING:
    from rhetorica_backend.contracts.metrics import MetricDefinition
    from rhetorica_backend.domain.chat.orchestration.state import (
        ToolOrchestrationState,
    )


class RunSqlPipelineSupport(Protocol):
    """Support contract implemented by ToolExecutor helpers."""

    def _prefer_context_connector(
        self,
        *,
        request: ChatAskRequest,
        requested_connector_name: str | None,
        sql: str,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem | None,
    ) -> ConnectorItem | None: ...

    def _sync_execution_zone(
        self,
        *,
        state: ToolOrchestrationState,
        db_connectors: list[ConnectorItem],
    ) -> None: ...

    def _unavailable_context_connector_error(
        self,
        *,
        request: ChatAskRequest,
        sql: str,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
    ) -> dict[str, Any] | None: ...

    def _requested_connector_name(
        self,
        arguments: dict[str, Any],
    ) -> str | None: ...

    def _normalize_sql_identifiers(
        self,
        *,
        sql: str,
        requested_connector_name: str | None,
        selected_connector: ConnectorItem,
        connection: Any,
    ) -> str: ...

    def _preflight_connector_name(
        self,
        *,
        arguments: dict[str, Any],
        selected_connector: ConnectorItem,
        connection: Any,
    ) -> str | None: ...

    def _preflight_metadata_context(
        self,
        *,
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
    ) -> tuple[list[str], dict[str, set[str]]]: ...

    def _hydrate_preflight_table_schemas(
        self,
        *,
        request: ChatAskRequest,
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
        connection: Any,
        sql: str,
        dialect: str,
    ) -> SqlStatementInspection: ...

    def _attempt_metadata_context_repair(
        self,
        *,
        request: ChatAskRequest,
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
        connection: Any,
        missing_tables: list[str],
    ) -> bool: ...

    def _attempt_sql_identifier_repair(
        self,
        *,
        sql: str,
        dialect: str,
        preflight_result: SqlPreflightResult,
    ) -> str | None: ...

    def _can_bypass_preflight_failure(
        self,
        *,
        preflight_result: SqlPreflightResult,
        state: ToolOrchestrationState,
        inspection: SqlStatementInspection,
    ) -> bool: ...

    def _preflight_error_payload(
        self,
        *,
        preflight_result: SqlPreflightResult,
        generated_sql: str,
        selected_connector_name: str,
        error_code: str,
        user_message: str,
    ) -> dict[str, Any]: ...

    def _tool_error_from_exception(
        self,
        exc: Exception,
        *,
        default_error_code: str,
        can_continue: bool,
    ) -> dict[str, Any]: ...

    def _hydrate_context_from_execution(
        self,
        *,
        state: ToolOrchestrationState,
        sql: str,
        execution: Any,
    ) -> None: ...

class RunSqlPipeline:
    """Orchestrates run_sql end-to-end while keeping ToolExecutor thin."""

    _RESOLVED_SUCCESS_WARNINGS: ClassVar[set[str]] = {
        "sql_execution_failed",
        "sql_execution_blocked_unavailable_context_connector",
        "sql_preflight_failed",
        "sql_preflight_uncertain",
    }

    def __init__(
        self,
        *,
        sql_router: SqlRouter,
        semantic_preflight_service: SemanticSqlPreflightService,
        support: RunSqlPipelineSupport,
        metric_registry_service: MetricRegistryService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._sql_router = sql_router
        self._semantic_preflight_service = semantic_preflight_service
        self._support = support
        self._metric_registry_service = metric_registry_service
        self._logger = logger or logging.getLogger(__name__)

    def execute(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        """Execute guarded SQL with semantic preflight and repair loop."""
        planning_context = state.build_sql_planning_context()

        if not request.run_sql:
            state.add_warning("sql_execution_not_allowed")
            return build_tool_error(
                error_code="sql_execution_not_allowed",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="SQL execution is disabled for this request.",
                retry_recommended=False,
                can_continue=True,
            )

        sql_value = arguments.get("sql")
        sql = sql_value.strip() if isinstance(sql_value, str) else None
        if not sql:
            state.add_warning("sql_missing")
            return build_tool_error(
                error_code="sql_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Tool argument sql is required.",
                retry_recommended=False,
                can_continue=True,
            )
        prompt_row_limit = normalize_sql_prompt_row_limit(arguments.get("prompt_row_limit"))
        execution_row_limit = normalize_sql_execution_row_limit(
            arguments.get("execution_row_limit")
        )
        if (
            arguments.get("prompt_row_limit") is not None
            and arguments.get("prompt_row_limit") != prompt_row_limit
        ):
            state.add_warning("sql_prompt_row_limit_clamped")
        if (
            arguments.get("execution_row_limit") is not None
            and arguments.get("execution_row_limit") != execution_row_limit
        ):
            state.add_warning("sql_execution_row_limit_clamped")

        requested_connector_name = self._support._requested_connector_name(arguments)

        self._logger.info(
            "sql.pipeline.started",
            extra={
                "workspace_id": request.workspace_id,
                "requested_connector_name": requested_connector_name,
                "question": request.question,
                "sql": sql,
            },
        )
        router_selected_connector = self._sql_router.select_connector(
            request=request,
            llm_payload={"target": arguments},
            db_connectors=db_connectors,
        )
        selected_connector = self._support._prefer_context_connector(
            request=request,
            requested_connector_name=requested_connector_name,
            sql=sql,
            db_connectors=db_connectors,
            state=state,
            selected_connector=router_selected_connector,
        )
        connector_overridden_from_context = (
            selected_connector is not None
            and router_selected_connector is not None
            and selected_connector.name != router_selected_connector.name
        )
        if selected_connector is None:
            state.add_warning("database_connector_not_selected")
            return build_tool_error(
                error_code="database_connector_not_selected",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Database connector could not be selected.",
                retry_recommended=False,
                can_continue=True,
            )

        if getattr(selected_connector.connection, "engine", None) != "trino":
            unavailable_context_error = self._support._unavailable_context_connector_error(
                request=request,
                sql=sql,
                db_connectors=db_connectors,
                state=state,
                selected_connector=selected_connector,
            )
            if unavailable_context_error is not None:
                return unavailable_context_error

        connection = self._sql_router.get_connection_for_execution(
            workspace_id=request.workspace_id,
            connector_name=selected_connector.name,
        )
        if connection is None:
            state.add_warning("database_connector_missing")
            return build_tool_error(
                error_code="database_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Selected database connector is missing.",
                retry_recommended=False,
                can_continue=True,
            )

        sql = self._support._normalize_sql_identifiers(
            sql=sql,
            requested_connector_name=requested_connector_name,
            selected_connector=selected_connector,
            connection=connection,
        )

        if getattr(selected_connector.connection, "engine", None) == "trino":
            unavailable_context_error = self._support._unavailable_context_connector_error(
                request=request,
                sql=sql,
                db_connectors=db_connectors,
                state=state,
                selected_connector=selected_connector,
            )
            if unavailable_context_error is not None:
                return unavailable_context_error

        state.selected_database_connector = selected_connector.name
        self._support._sync_execution_zone(
            state=state,
            db_connectors=db_connectors,
        )

        state.generated_sql = sql
        approval_payload = self._approval_gate_payload(
            state=state,
            arguments=arguments,
            sql=sql,
            selected_connector_name=selected_connector.name,
        )
        if approval_payload is not None:
            return approval_payload

        dialect = sql_dialect_for_engine(connection.engine)
        inspection = self._support._hydrate_preflight_table_schemas(
            request=request,
            state=state,
            selected_connector=selected_connector,
            connection=connection,
            sql=sql,
            dialect=dialect,
        )
        preflight_context_tables, preflight_metadata_columns = (
            self._support._preflight_metadata_context(
                state=state,
                selected_connector=selected_connector,
            )
        )
        if not preflight_context_tables:
            preflight_context_tables = list(planning_context.context_tables)
        if not preflight_metadata_columns:
            preflight_metadata_columns = {
                table_name: set(columns)
                for table_name, columns in planning_context.metadata_table_columns.items()
            }

        preflight_result = self._semantic_preflight_service.check(
            sql=sql,
            dialect=dialect,
            engine=connection.engine,
            context_tables=preflight_context_tables,
            metadata_table_columns=preflight_metadata_columns,
            requested_connector_name=(
                selected_connector.name
                if connector_overridden_from_context
                else self._support._preflight_connector_name(
                    arguments=arguments,
                    selected_connector=selected_connector,
                    connection=connection,
                )
            ),
            selected_connector_name=selected_connector.name,
        )

        # Discover metrics from business terms and run consistency checks
        discovered_metrics = self._discover_metrics_for_question(
            question=request.question,
            workspace_id=request.workspace_id,
        )
        if discovered_metrics:
            # Store compact metric cards in state for metadata context injection
            if self._metric_registry_service is not None:
                state.discovered_metrics = [
                    self._metric_registry_service.get_compact_card(metric)
                    for metric in discovered_metrics
                ]
            for metric in discovered_metrics:
                metric_result = self._semantic_preflight_service.check_metric_consistency(
                    sql=sql,
                    metric_id=metric.metric_id,
                    metric_name=metric.name,
                    metric_allowed_dimensions=metric.allowed_dimensions,
                    metric_source_tables=metric.source_tables,
                    context_tables=preflight_context_tables,
                    metadata_table_columns=preflight_metadata_columns,
                )
                if metric_result.errors:
                    preflight_result.errors.extend(metric_result.errors)
                    preflight_result.status = "fail"
                    self._logger.info(
                        "sql.pipeline.metric_violation",
                        extra={
                            "metric_id": metric.metric_id,
                            "metric_name": metric.name,
                            "errors": sanitize_for_logging(metric_result.errors),
                        },
                    )

        preflight_status = preflight_result.status
        self._logger.info(
            "sql.pipeline.preflight.completed",
            extra={
                "selected_connector_name": selected_connector.name,
                "status": preflight_result.status,
                "errors": sanitize_for_logging(preflight_result.errors),
                "referenced_tables": preflight_result.referenced_tables,
            },
        )
        if preflight_result.status in {"fail", "uncertain"}:
            repair_targets = self._repairable_missing_tables(preflight_result)
            repaired_context = False
            if repair_targets:
                repaired_context = self._support._attempt_metadata_context_repair(
                    request=request,
                    state=state,
                    selected_connector=selected_connector,
                    connection=connection,
                    missing_tables=repair_targets,
                )
            if repaired_context:
                self._logger.info(
                    "sql.pipeline.context_repaired",
                    extra={
                        "selected_connector_name": selected_connector.name,
                        "missing_tables": repair_targets,
                    },
                )
                state.add_warning("sql_preflight_context_repaired")
                preflight_context_tables, preflight_metadata_columns = (
                    self._support._preflight_metadata_context(
                        state=state,
                        selected_connector=selected_connector,
                    )
                )
                preflight_result = self._semantic_preflight_service.check(
                    sql=sql,
                    dialect=dialect,
                    engine=connection.engine,
                    context_tables=preflight_context_tables,
                    metadata_table_columns=preflight_metadata_columns,
                    requested_connector_name=(
                        selected_connector.name
                        if connector_overridden_from_context
                        else self._support._preflight_connector_name(
                            arguments=arguments,
                            selected_connector=selected_connector,
                            connection=connection,
                        )
                    ),
                    selected_connector_name=selected_connector.name,
                )
                preflight_status = preflight_result.status
        if preflight_result.status == "fail":
            repaired_sql = self._support._attempt_sql_identifier_repair(
                sql=sql,
                dialect=dialect,
                preflight_result=preflight_result,
            )
            if repaired_sql and repaired_sql != sql:
                self._logger.info(
                    "sql.pipeline.identifier_repaired",
                    extra={
                        "selected_connector_name": selected_connector.name,
                        "original_sql": sql,
                        "repaired_sql": repaired_sql,
                    },
                )
                sql = repaired_sql
                state.generated_sql = repaired_sql
                state.add_warning("sql_preflight_identifier_repaired")
                preflight_context_tables, preflight_metadata_columns = (
                    self._support._preflight_metadata_context(
                        state=state,
                        selected_connector=selected_connector,
                    )
                )
                preflight_result = self._semantic_preflight_service.check(
                    sql=sql,
                    dialect=dialect,
                    engine=connection.engine,
                    context_tables=preflight_context_tables,
                    metadata_table_columns=preflight_metadata_columns,
                    requested_connector_name=(
                        selected_connector.name
                        if connector_overridden_from_context
                        else self._support._preflight_connector_name(
                            arguments=arguments,
                            selected_connector=selected_connector,
                            connection=connection,
                        )
                    ),
                    selected_connector_name=selected_connector.name,
                )
                preflight_status = preflight_result.status
        if self._support._can_bypass_preflight_failure(
            preflight_result=preflight_result,
            state=state,
            inspection=inspection,
        ):
            state.preflight_uncertain += 1
            state.add_warning(self._preflight_bypass_warning(preflight_result))
            preflight_status = "pass"
        if preflight_result.status == "fail" and preflight_status != "pass":
            state.preflight_fail += 1
            state.add_warning("sql_preflight_failed")
            return self._support._preflight_error_payload(
                preflight_result=preflight_result,
                generated_sql=sql,
                selected_connector_name=selected_connector.name,
                error_code="sql_preflight_failed",
                user_message="SQL preflight failed. Fix table/column/function issues and retry.",
            )
        if preflight_result.status == "uncertain" and preflight_status != "pass":
            state.preflight_uncertain += 1
            state.add_warning("sql_preflight_uncertain")
            return self._support._preflight_error_payload(
                preflight_result=preflight_result,
                generated_sql=sql,
                selected_connector_name=selected_connector.name,
                error_code="sql_preflight_uncertain",
                user_message=(
                    "SQL preflight is blocked because metadata context is incomplete. "
                    "Refresh table metadata or confirm the target table before retrying."
                ),
            )
        state.preflight_pass += 1
        sql_for_execution_plan = sql

        try:
            execution = self._sql_router.execute_sql(
                sql=sql,
                connection=connection,
                workspace_id=request.workspace_id,
                dialect=dialect,
                row_limit=execution_row_limit,
                conversation_id=state.conversation_id,
            )
        except Exception as exc:
            self._logger.exception("SQL execution failed")
            state.add_warning("sql_execution_failed")
            payload = self._support._tool_error_from_exception(
                exc,
                default_error_code="sql_execution_failed",
                can_continue=False,
            )
            payload["error_code"] = "sql_execution_failed"
            payload["error"] = "sql_execution_failed"
            payload["error_category"] = ErrorCategory.FATAL.value
            payload["retry_recommended"] = False
            payload["user_message"] = "SQL execution failed. Check query and connector settings."
            payload["can_continue"] = False
            return payload

        state.record_execution_outcome(
            sql=sql,
            connector_name=selected_connector.name,
            execution=execution,
            preflight_status=preflight_status,
        )
        execution_succeeded = execution.status == "succeeded"
        if not execution_succeeded:
            state.add_warning("sql_execution_failed")
        else:
            self._clear_resolved_success_warnings(state)
        self._support._hydrate_context_from_execution(
            state=state,
            sql=sql,
            execution=execution,
        )
        self._support._sync_execution_zone(
            state=state,
            db_connectors=db_connectors,
        )
        self._logger.info(
            "sql.pipeline.completed",
            extra={
                "selected_connector_name": selected_connector.name,
                "preflight_status": preflight_status,
                "execution_status": execution.status,
                "execution_id": execution.execution_id,
                "query_id": execution.query_id,
            },
        )

        result = {
            "ok": execution_succeeded,
            "generated_sql": sql_for_execution_plan,
            "executed_sql": sql,
            "selected_database_connector": selected_connector.name,
            "preflight_status": preflight_status,
            "execution": execution.model_dump(mode="json"),
            "prompt_row_limit": prompt_row_limit,
            "execution_row_limit": execution_row_limit,
        }
        if not execution_succeeded:
            result.update({
                "error": "sql_execution_failed",
                "error_code": "sql_execution_failed",
                "error_category": ErrorCategory.RECOVERABLE.value,
                "error_message": execution.error_message,
                "user_message": (
                    "SQL execution failed. Inspect execution.error_message, fix the SQL "
                    "or connector choice, and retry with changed arguments."
                ),
                "retry_recommended": True,
                "can_continue": True,
                "next_allowed_actions": [
                    "inspect_execution_error",
                    "fix_sql",
                    "change_connector",
                    "fetch_metadata",
                    "answer_with_failure_explanation",
                ],
            })
        return result

    def _approval_gate_payload(
        self,
        *,
        state: ToolOrchestrationState,
        arguments: dict[str, Any],
        sql: str,
        selected_connector_name: str,
    ) -> dict[str, Any] | None:
        _ = state, arguments, sql, selected_connector_name
        return None

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @classmethod
    def _clear_resolved_success_warnings(cls, state: ToolOrchestrationState) -> None:
        state.warnings[:] = [
            warning
            for warning in state.warnings
            if warning not in cls._RESOLVED_SUCCESS_WARNINGS
        ]

    @staticmethod
    def _repairable_missing_tables(preflight_result: SqlPreflightResult) -> list[str]:
        error_codes = {item.code for item in preflight_result.errors}
        if error_codes.intersection({"missing_table", "missing_column"}):
            return list(preflight_result.referenced_tables)
        if error_codes and error_codes == {"metadata_context_missing"}:
            return list(preflight_result.referenced_tables)
        return []

    @staticmethod
    def _preflight_bypass_warning(preflight_result: SqlPreflightResult) -> str:
        if preflight_result.status == "uncertain":
            return "sql_preflight_uncertain_bypassed"
        error_codes = {item.code for item in preflight_result.errors}
        if error_codes == {"missing_table"}:
            return "sql_preflight_missing_table_bypassed"
        if error_codes == {"missing_column"}:
            return "sql_preflight_missing_column_bypassed"
        if error_codes and error_codes.issubset({"missing_table", "missing_column"}):
            return "sql_preflight_schema_bypassed"
        return "sql_preflight_uncertain_bypassed"

    def _discover_metrics_for_question(
        self,
        question: str,
        workspace_id: str,
    ) -> list[MetricDefinition]:
        """Discover metrics by matching business terms in the question.

        Uses fuzzy string matching to find metrics whose names or descriptions
        semantically match terms in the user's question.

        Args:
            question: The user's question text.
            workspace_id: The workspace identifier.

        Returns:
            List of discovered metric definitions that match business terms.
        """
        if self._metric_registry_service is None:
            return []

        return self._metric_registry_service.find_by_business_term(
            term=question,
            workspace_id=workspace_id,
        )
