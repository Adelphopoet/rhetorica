"""Airflow-family orchestration tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.airflow import AirflowRequestError, AirflowService
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    is_transient_http_status,
    retry_idempotent,
)
from rhetorica_backend.domain.chat.orchestration.agent_connector_policy import (
    AgentConnectorPolicy,
)
from rhetorica_backend.infra.postgres.airflow_connector_repository import (
    AirflowConnectorRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.contracts.admin_integrations import AirflowConnectionConfig
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolAirflowCapabilityService:
    """Capability-family service for read-only Airflow tools."""

    def __init__(
        self,
        *,
        airflow_connector_repository: AirflowConnectorRepository | None,
        airflow_service: AirflowService | None = None,
        bounded_int: Callable[..., int],
        tool_error_from_exception: Callable[..., dict[str, Any]],
    ) -> None:
        self._airflow_connector_repository = airflow_connector_repository
        self._airflow_service = airflow_service or AirflowService()
        self._bounded_int = bounded_int
        self._tool_error_from_exception = tool_error_from_exception

    def execute_search_airflow_dags(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        connection = cast("AirflowConnectionConfig", connection)
        try:
            payload = self._retry_airflow_read(
                lambda: self._airflow_service.search_dags(
                    connection,
                    query=self._optional_str(arguments.get("query")),
                    limit=self._bounded_int(
                        arguments.get("limit"), default=connection.dag_limit, minimum=1, maximum=100
                    ),
                    offset=self._bounded_int(
                        arguments.get("offset"), default=0, minimum=0, maximum=100000
                    ),
                    only_active=arguments.get("only_active") is not False,
                    paused=self._optional_bool(arguments.get("paused")),
                    tags=self._string_list(arguments.get("tags")),
                )
            )
        except AirflowRequestError as exc:
            state.warnings.append("airflow_dag_search_failed")
            return self._airflow_request_error_result(exc, "airflow_dag_search_failed")
        except Exception as exc:
            state.warnings.append("airflow_dag_search_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="airflow_dag_search_failed",
                can_continue=True,
            )
        return self._ok(connector_name=connector_name, payload=payload, state=state)

    def execute_get_airflow_dag(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        dag_id = self._required_str(arguments.get("dag_id"), "airflow_dag_id_missing")
        if isinstance(dag_id, dict):
            return dag_id
        connection = cast("AirflowConnectionConfig", connection)
        return self._call(
            lambda: self._airflow_service.get_dag(connection, dag_id=dag_id),
            connector_name=connector_name,
            state=state,
            warning="airflow_get_dag_failed",
        )

    def execute_list_airflow_dag_runs(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        dag_id = self._required_str(arguments.get("dag_id"), "airflow_dag_id_missing")
        if isinstance(dag_id, dict):
            return dag_id
        connection = cast("AirflowConnectionConfig", connection)
        return self._call(
            lambda: self._airflow_service.list_dag_runs(
                connection,
                dag_id=dag_id,
                limit=self._bounded_int(
                    arguments.get("limit"), default=connection.dag_run_limit, minimum=1, maximum=100
                ),
                offset=self._bounded_int(
                    arguments.get("offset"), default=0, minimum=0, maximum=100000
                ),
                state=self._optional_str(arguments.get("state")),
                order_by=self._optional_str(arguments.get("order_by")) or "-start_date",
            ),
            connector_name=connector_name,
            state=state,
            warning="airflow_dag_runs_failed",
        )

    def execute_get_airflow_dag_run(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        ids = self._required_ids(arguments, ("dag_id", "dag_run_id"))
        if isinstance(ids, dict):
            return ids
        connection = cast("AirflowConnectionConfig", connection)
        return self._call(
            lambda: self._airflow_service.get_dag_run(
                connection,
                dag_id=ids[0],
                dag_run_id=ids[1],
            ),
            connector_name=connector_name,
            state=state,
            warning="airflow_get_dag_run_failed",
        )

    def execute_list_airflow_task_instances(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        ids = self._required_ids(arguments, ("dag_id", "dag_run_id"))
        if isinstance(ids, dict):
            return ids
        connection = cast("AirflowConnectionConfig", connection)
        return self._call(
            lambda: self._airflow_service.list_task_instances(
                connection,
                dag_id=ids[0],
                dag_run_id=ids[1],
                limit=self._bounded_int(
                    arguments.get("limit"), default=connection.task_limit, minimum=1, maximum=200
                ),
                offset=self._bounded_int(
                    arguments.get("offset"), default=0, minimum=0, maximum=100000
                ),
                state=self._optional_str(arguments.get("state")),
            ),
            connector_name=connector_name,
            state=state,
            warning="airflow_task_instances_failed",
        )

    def execute_get_airflow_task_logs(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        ids = self._required_ids(arguments, ("dag_id", "dag_run_id", "task_id"))
        if isinstance(ids, dict):
            return ids
        try_number = self._bounded_int(
            arguments.get("try_number"), default=1, minimum=1, maximum=1000
        )
        connection = cast("AirflowConnectionConfig", connection)
        return self._call(
            lambda: self._airflow_service.get_task_logs(
                connection,
                dag_id=ids[0],
                dag_run_id=ids[1],
                task_id=ids[2],
                try_number=try_number,
                full_content=arguments.get("full_content") is not False,
                max_chars=self._bounded_int(
                    arguments.get("max_chars"),
                    default=connection.log_max_chars,
                    minimum=1000,
                    maximum=50000,
                ),
                offset=self._bounded_int(
                    arguments.get("offset"),
                    default=0,
                    minimum=0,
                    maximum=5_000_000,
                ),
                continuation_token=self._optional_str(arguments.get("continuation_token")),
            ),
            connector_name=connector_name,
            state=state,
            warning="airflow_task_logs_failed",
        )

    def execute_list_airflow_import_errors(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        connection = cast("AirflowConnectionConfig", connection)
        return self._call(
            lambda: self._airflow_service.list_import_errors(
                connection,
                limit=self._bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100),
                offset=self._bounded_int(
                    arguments.get("offset"), default=0, minimum=0, maximum=100000
                ),
            ),
            connector_name=connector_name,
            state=state,
            warning="airflow_import_errors_failed",
        )

    def execute_get_airflow_health(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        connection = cast("AirflowConnectionConfig", connection)
        return self._call(
            lambda: self._airflow_service.get_health(connection),
            connector_name=connector_name,
            state=state,
            warning="airflow_health_failed",
        )

    def _call(
        self,
        call: Callable[[], dict[str, Any]],
        *,
        connector_name: str,
        state: ToolOrchestrationState,
        warning: str,
    ) -> dict[str, Any]:
        try:
            payload = self._retry_airflow_read(call)
        except AirflowRequestError as exc:
            state.warnings.append(warning)
            return self._airflow_request_error_result(exc, warning)
        except Exception as exc:
            state.warnings.append(warning)
            return self._tool_error_from_exception(
                exc,
                default_error_code=warning,
                can_continue=True,
            )
        return self._ok(connector_name=connector_name, payload=payload, state=state)

    @staticmethod
    def _retry_airflow_read(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        last_exc: AirflowRequestError | None = None
        for attempt in range(2):
            try:
                return retry_idempotent(call, max_attempts=2)
            except AirflowRequestError as exc:
                last_exc = exc
                if attempt >= 1 or not is_transient_http_status(exc.status_code):
                    raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError

    def _resolve_connection(
        self,
        *,
        request: ChatAskRequest,
        connector_name: Any,
        state: ToolOrchestrationState,
    ) -> tuple[AirflowConnectionConfig | None, str, dict[str, Any] | None]:
        if self._airflow_connector_repository is None:
            state.warnings.append("airflow_connector_missing")
            return (
                None,
                "",
                build_tool_error(
                    error_code="airflow_connector_missing",
                    error_category=ErrorCategory.RECOVERABLE,
                    user_message="No Airflow connector is configured.",
                    retry_recommended=False,
                    can_continue=True,
                ),
            )

        decision = AgentConnectorPolicy.resolve_connector_name(
            requested_name=connector_name,
            allowed_names=request.agent_allowed_airflow_connector_names,
            family="airflow",
        )
        if not decision.allowed:
            if decision.warning:
                state.warnings.append(decision.warning)
            return (
                None,
                "",
                build_tool_error(
                    error_code=decision.warning or "agent_airflow_connector_not_allowed",
                    error_category=ErrorCategory.RECOVERABLE,
                    user_message="This agent cannot use the requested Airflow connector.",
                    retry_recommended=False,
                    can_continue=True,
                ),
            )

        connection = self._airflow_connector_repository.get_connection(
            workspace_id=request.workspace_id,
            name=decision.connector_name,
        )
        if connection is None:
            state.warnings.append("airflow_connector_missing")
            return (
                None,
                "",
                build_tool_error(
                    error_code="airflow_connector_missing",
                    error_category=ErrorCategory.RECOVERABLE,
                    user_message="Requested Airflow connector is unavailable.",
                    retry_recommended=False,
                    can_continue=True,
                ),
            )
        resolved_name = decision.connector_name
        if resolved_name is None:
            item = self._airflow_connector_repository.list(request.workspace_id, limit=1)
            resolved_name = item[0].name if item else "airflow"
        return connection, resolved_name, None

    def _ok(
        self,
        *,
        connector_name: str,
        payload: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        state.metadata_sources.add("airflow")
        return {
            "ok": True,
            "connector_name": connector_name,
            "airflow": payload,
            "metadata_sources": sorted(state.metadata_sources),
        }

    @staticmethod
    def _airflow_request_error_result(
        exc: AirflowRequestError,
        default_error_code: str,
    ) -> dict[str, Any]:
        result = build_tool_error(
            error_code=default_error_code,
            error_category=ErrorCategory.TRANSIENT,
            user_message="Airflow API request failed.",
            retry_recommended=exc.status_code >= 500,
            can_continue=True,
        )
        result["airflow_error"] = exc.to_payload()
        return result

    @staticmethod
    def _required_ids(
        arguments: dict[str, Any],
        names: tuple[str, ...],
    ) -> tuple[str, ...] | dict[str, Any]:
        values: list[str] = []
        for name in names:
            value = arguments.get(name)
            if not isinstance(value, str) or not value.strip():
                return build_tool_error(
                    error_code=f"airflow_{name}_missing",
                    error_category=ErrorCategory.RECOVERABLE,
                    user_message=f"Airflow {name} is required.",
                    retry_recommended=False,
                    can_continue=True,
                )
            values.append(value.strip())
        return tuple(values)

    @staticmethod
    def _required_str(value: Any, error_code: str) -> str | dict[str, Any]:
        if not isinstance(value, str) or not value.strip():
            return build_tool_error(
                error_code=error_code,
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Required Airflow argument is missing.",
                retry_recommended=False,
                can_continue=True,
            )
        return value.strip()

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _optional_bool(value: Any) -> bool | None:
        return value if isinstance(value, bool) else None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
