from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from rhetorica_backend.contracts.admin_integrations import AirflowConnectionConfig
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.airflow import AirflowRequestError
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_airflow_capability_service import (
    ToolAirflowCapabilityService,
)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _error_from_exception(
    _exc: Exception,
    *,
    default_error_code: str,
    can_continue: bool,
) -> dict[str, Any]:
    return {"ok": False, "error_code": default_error_code, "can_continue": can_continue}


class _Repository:
    def __init__(self, connection: AirflowConnectionConfig | None) -> None:
        self.connection = connection
        self.requested_name: str | None = None

    def get_connection(
        self,
        *,
        workspace_id: str,
        name: str | None = None,
    ) -> AirflowConnectionConfig | None:
        assert workspace_id == "default"
        self.requested_name = name
        return self.connection

    def list(self, workspace_id: str, limit: int) -> list[Any]:
        assert workspace_id == "default"
        assert limit == 1
        return [SimpleNamespace(name="airflow-main")]


class _AirflowService:
    def search_dags(self, _connection: AirflowConnectionConfig, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["limit"] == 100
        assert kwargs["query"] == "sales"
        return {"dags": [{"dag_id": "sales"}]}

    def get_dag(self, _connection: AirflowConnectionConfig, **kwargs: Any) -> dict[str, Any]:
        return {"dag_id": kwargs["dag_id"]}

    def list_dag_runs(self, _connection: AirflowConnectionConfig, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["limit"] == 100
        return {"dag_runs": [{"dag_run_id": kwargs["dag_id"]}]}

    def get_dag_run(self, _connection: AirflowConnectionConfig, **kwargs: Any) -> dict[str, Any]:
        return {"dag_run_id": kwargs["dag_run_id"]}

    def list_task_instances(
        self,
        _connection: AirflowConnectionConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert kwargs["limit"] == 200
        return {"task_instances": [{"task_id": kwargs["dag_run_id"]}]}

    def get_task_logs(self, _connection: AirflowConnectionConfig, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["max_chars"] == 50000
        assert kwargs["offset"] == 5000000
        assert kwargs["continuation_token"] == "token-1"
        return {"content": "bounded"}

    def list_import_errors(
        self,
        _connection: AirflowConnectionConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert kwargs["limit"] == 100
        return {"import_errors": []}

    def get_health(self, _connection: AirflowConnectionConfig) -> dict[str, Any]:
        return {"healthy": True}


class _TransientAirflowService:
    def __init__(self) -> None:
        self.calls = 0

    def search_dags(self, _connection: AirflowConnectionConfig, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            raise AirflowRequestError(
                status_code=503,
                request_path="/api/v1/dags",
                request_params={},
                response_body="scheduler unavailable",
            )
        return {"dags": [{"dag_id": "sales"}]}


def _connection() -> AirflowConnectionConfig:
    return AirflowConnectionConfig(
        provider="airflow",
        base_url="https://airflow.example.com",
        username="airflow-user",
        password="secret-password",
        use_when="Use for read-only Airflow inspection.",
    )


def _service(repository: _Repository | None) -> ToolAirflowCapabilityService:
    return ToolAirflowCapabilityService(
        airflow_connector_repository=repository,  # type: ignore[arg-type]
        airflow_service=_AirflowService(),  # type: ignore[arg-type]
        bounded_int=_bounded_int,
        tool_error_from_exception=_error_from_exception,
    )


def test_airflow_tools_respect_allowlist_bound_limits_and_record_source() -> None:
    repository = _Repository(_connection())
    request = ChatAskRequest(
        question="check airflow",
        workspace_id="default",
        agent_allowed_airflow_connector_names=["airflow-main"],
    )
    state = ToolOrchestrationState()
    service = _service(repository)

    results = [
        service.execute_search_airflow_dags(
            request=request,
            arguments={"connector_name": "airflow-main", "query": "sales", "limit": 999},
            state=state,
        ),
        service.execute_get_airflow_dag(
            request=request,
            arguments={"connector_name": "airflow-main", "dag_id": "sales"},
            state=state,
        ),
        service.execute_list_airflow_dag_runs(
            request=request,
            arguments={"connector_name": "airflow-main", "dag_id": "sales", "limit": 999},
            state=state,
        ),
        service.execute_get_airflow_dag_run(
            request=request,
            arguments={
                "connector_name": "airflow-main",
                "dag_id": "sales",
                "dag_run_id": "run-1",
            },
            state=state,
        ),
        service.execute_list_airflow_task_instances(
            request=request,
            arguments={
                "connector_name": "airflow-main",
                "dag_id": "sales",
                "dag_run_id": "run-1",
                "limit": 999,
            },
            state=state,
        ),
        service.execute_get_airflow_task_logs(
            request=request,
            arguments={
                "connector_name": "airflow-main",
                "dag_id": "sales",
                "dag_run_id": "run-1",
                "task_id": "load",
                "max_chars": 999999,
                "offset": 999999999,
                "continuation_token": " token-1 ",
            },
            state=state,
        ),
        service.execute_list_airflow_import_errors(
            request=request,
            arguments={"connector_name": "airflow-main", "limit": 999},
            state=state,
        ),
        service.execute_get_airflow_health(
            request=request,
            arguments={"connector_name": "airflow-main"},
            state=state,
        ),
    ]

    assert all(result["ok"] is True for result in results)
    assert all(result["connector_name"] == "airflow-main" for result in results)
    assert all(result["metadata_sources"] == ["airflow"] for result in results)
    assert state.metadata_sources == {"airflow"}
    assert repository.requested_name == "airflow-main"


def test_airflow_tool_rejects_connector_outside_agent_allowlist() -> None:
    request = ChatAskRequest(
        question="check airflow",
        workspace_id="default",
        agent_allowed_airflow_connector_names=["airflow-main"],
    )
    state = ToolOrchestrationState()

    result = _service(_Repository(_connection())).execute_get_airflow_health(
        request=request,
        arguments={"connector_name": "airflow-dev"},
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "agent_airflow_connector_not_allowed"
    assert state.warnings == ["agent_airflow_connector_not_allowed"]


def test_airflow_tool_retries_transient_airflow_http_errors() -> None:
    airflow_service = _TransientAirflowService()
    service = ToolAirflowCapabilityService(
        airflow_connector_repository=_Repository(_connection()),  # type: ignore[arg-type]
        airflow_service=airflow_service,  # type: ignore[arg-type]
        bounded_int=_bounded_int,
        tool_error_from_exception=_error_from_exception,
    )

    result = service.execute_search_airflow_dags(
        request=ChatAskRequest(
            question="check airflow",
            workspace_id="default",
            agent_allowed_airflow_connector_names=["airflow-main"],
        ),
        arguments={"connector_name": "airflow-main", "query": "sales"},
        state=ToolOrchestrationState(),
    )

    assert result["ok"] is True
    assert result["airflow"]["dags"] == [{"dag_id": "sales"}]
    assert airflow_service.calls == 2
