from __future__ import annotations

from typing import Any

import httpx
import pytest
from rhetorica_backend.contracts.admin_integrations import AirflowConnectionConfig
from rhetorica_backend.domain.chat.airflow import AirflowRequestError, AirflowService


class _FakeClient:
    calls: list[dict[str, Any]]
    responses: list[httpx.Response]

    def __init__(self, *, timeout: float, verify: bool) -> None:
        self.timeout = timeout
        self.verify = verify

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({
            "method": "GET",
            "url": url,
            "params": kwargs.get("params"),
            "headers": dict(kwargs.get("headers") or {}),
            "auth": kwargs.get("auth"),
            "timeout": self.timeout,
            "verify": self.verify,
        })
        return self.responses.pop(0)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({
            "method": "POST",
            "url": url,
            "json": kwargs.get("json"),
            "headers": dict(kwargs.get("headers") or {}),
            "timeout": self.timeout,
            "verify": self.verify,
        })
        return self.responses.pop(0)


def _connection() -> AirflowConnectionConfig:
    return AirflowConnectionConfig(
        provider="airflow",
        base_url="https://airflow.example.com/",
        username="airflow-user",
        password="secret-password",
        use_when="Use for read-only Airflow inspection.",
        verify_tls=False,
        api_path="/api/v1",
        dag_limit=5,
        dag_run_limit=4,
        task_limit=3,
        log_max_chars=1000,
        timeout_seconds=9,
    )


def _json_response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload, headers={"content-type": "application/json"})


def test_airflow_service_uses_get_only_auth_headers_and_bounded_paths() -> None:
    _FakeClient.calls = []
    _FakeClient.responses = [
        _json_response({"dags": [{"dag_id": "sales"}], "total_entries": 1}),
        _json_response({"dag_id": "sales"}),
        _json_response({"dag_runs": [{"dag_run_id": "run-1"}], "total_entries": 1}),
        _json_response({"dag_run_id": "run-1"}),
        _json_response({"task_instances": [{"task_id": "load"}], "total_entries": 1}),
        httpx.Response(200, text="x" * 1200),
        _json_response({"import_errors": [{"filename": "bad.py"}], "total_entries": 1}),
        _json_response({"metadatabase": {"status": "healthy"}}),
    ]
    service = AirflowService(client_factory=_FakeClient)
    connection = _connection()

    dags = service.search_dags(connection, query="sales", limit=999, tags=["prod", "prod"])
    dag = service.get_dag(connection, dag_id="sales/daily")
    runs = service.list_dag_runs(connection, dag_id="sales/daily", limit=999, state="failed")
    run = service.get_dag_run(connection, dag_id="sales/daily", dag_run_id="manual/run")
    tasks = service.list_task_instances(
        connection,
        dag_id="sales/daily",
        dag_run_id="manual/run",
        limit=999,
    )
    logs = service.get_task_logs(
        connection,
        dag_id="sales/daily",
        dag_run_id="manual/run",
        task_id="load/task",
        try_number=1,
        max_chars=1,
        offset=10,
        continuation_token="token-1",
    )
    errors = service.list_import_errors(connection, limit=999)
    health = service.get_health(connection)

    assert dags["limit"] == 100
    assert dags["dags"][0]["dag_id"] == "sales"
    assert dag["dag_id"] == "sales"
    assert runs["limit"] == 100
    assert run["dag_run_id"] == "run-1"
    assert tasks["limit"] == 200
    assert logs["content"] == "x" * 1000
    assert logs["truncated"] is True
    assert logs["max_chars"] == 1000
    assert logs["offset"] == 10
    assert logs["next_offset"] == 1010
    assert logs["content_length"] == 1200
    assert errors["limit"] == 100
    assert health["metadatabase"]["status"] == "healthy"

    paths = [call["url"].removeprefix("https://airflow.example.com") for call in _FakeClient.calls]
    assert paths == [
        "/api/v1/dags",
        "/api/v1/dags/sales%2Fdaily",
        "/api/v1/dags/sales%2Fdaily/dagRuns",
        "/api/v1/dags/sales%2Fdaily/dagRuns/manual%2Frun",
        "/api/v1/dags/sales%2Fdaily/dagRuns/manual%2Frun/taskInstances",
        "/api/v1/dags/sales%2Fdaily/dagRuns/manual%2Frun/taskInstances/load%2Ftask/logs/1",
        "/api/v1/importErrors",
        "/health",
    ]
    assert {call["method"] for call in _FakeClient.calls} == {"GET"}
    assert _FakeClient.calls[0]["headers"]["Accept"] == "application/json"
    assert _FakeClient.calls[5]["params"] == {
        "full_content": True,
        "continuation_token": "token-1",
    }
    assert isinstance(_FakeClient.calls[0]["auth"], httpx.BasicAuth)
    assert _FakeClient.calls[0]["timeout"] == 9
    assert _FakeClient.calls[0]["verify"] is False
    assert not hasattr(service, "post")
    assert not hasattr(service, "request")


def test_airflow_service_raises_structured_http_errors() -> None:
    _FakeClient.calls = []
    _FakeClient.responses = [httpx.Response(503, text="temporarily down")]
    service = AirflowService(client_factory=_FakeClient)

    with pytest.raises(AirflowRequestError) as exc_info:
        service.get_dag(_connection(), dag_id="sales")

    payload = exc_info.value.to_payload()
    assert payload["status_code"] == 503
    assert payload["request_path"] == "/api/v1/dags/sales"
    assert payload["response_body"] == "temporarily down"


def test_airflow_service_uses_v2_health_when_api_path_is_v2() -> None:
    _FakeClient.calls = []
    _FakeClient.responses = [
        _json_response({"access_token": "jwt-token"}, status_code=201),
        _json_response({"scheduler": {"status": "healthy"}}),
    ]
    service = AirflowService(client_factory=_FakeClient)
    connection = _connection().model_copy(update={"api_path": "/api/v2"})

    health = service.get_health(connection)

    assert health["scheduler"]["status"] == "healthy"
    assert _FakeClient.calls[0]["method"] == "POST"
    assert _FakeClient.calls[0]["url"] == "https://airflow.example.com/auth/token"
    assert _FakeClient.calls[0]["json"] == {
        "username": "airflow-user",
        "password": "secret-password",
    }
    assert _FakeClient.calls[1]["url"] == "https://airflow.example.com/api/v2/monitor/health"
    assert _FakeClient.calls[1]["auth"] is None
    assert _FakeClient.calls[1]["headers"]["Authorization"] == "Bearer jwt-token"


def test_airflow_service_reuses_airflow3_token_for_get_requests() -> None:
    _FakeClient.calls = []
    _FakeClient.responses = [
        _json_response({"access_token": "jwt-token"}, status_code=201),
        _json_response({"dags": [{"dag_id": "sales"}], "total_entries": 1}),
        _json_response({"dags": [{"dag_id": "sales"}], "total_entries": 1}),
    ]
    service = AirflowService(client_factory=_FakeClient)
    connection = _connection().model_copy(update={"api_path": "/api/v2"})

    first = service.search_dags(connection, limit=1)
    second = service.search_dags(connection, limit=1)

    assert first["dags"] == second["dags"] == [{"dag_id": "sales"}]
    assert [call["method"] for call in _FakeClient.calls] == ["POST", "GET", "GET"]
    assert _FakeClient.calls[1]["headers"]["Authorization"] == "Bearer jwt-token"
    assert _FakeClient.calls[2]["headers"]["Authorization"] == "Bearer jwt-token"


def test_airflow_service_refreshes_cached_airflow3_token_after_unauthorized() -> None:
    _FakeClient.calls = []
    _FakeClient.responses = [
        _json_response({"access_token": "old-token"}, status_code=201),
        httpx.Response(401, text="token expired"),
        _json_response({"access_token": "new-token"}, status_code=201),
        _json_response({"dags": [{"dag_id": "sales"}], "total_entries": 1}),
    ]
    service = AirflowService(client_factory=_FakeClient)
    connection = _connection().model_copy(update={"api_path": "/api/v2"})

    dags = service.search_dags(connection, limit=1)

    assert dags["dags"] == [{"dag_id": "sales"}]
    assert [call["method"] for call in _FakeClient.calls] == ["POST", "GET", "POST", "GET"]
    assert _FakeClient.calls[1]["headers"]["Authorization"] == "Bearer old-token"
    assert _FakeClient.calls[3]["headers"]["Authorization"] == "Bearer new-token"


def test_airflow_service_marks_end_of_log_token_as_not_truncated() -> None:
    _FakeClient.calls = []
    _FakeClient.responses = [
        _json_response({
            "content": [
                ["", " INFO - ::group::Log message source details\n*** No logs found on s3\n"]
            ],
            "continuation_token": "eyJlbmRfb2ZfbG9nIjp0cnVlLCJsb2dfcG9zIjowfQ.sig",
        }),
    ]
    service = AirflowService(client_factory=_FakeClient)

    logs = service.get_task_logs(
        _connection(),
        dag_id="sales",
        dag_run_id="scheduled__2026-05-30T01:00:00+00:00",
        task_id="Data_Marts.sales_dm",
        try_number=1,
        max_chars=5000,
    )

    assert "No logs found on s3" in logs["content"]
    assert logs["end_of_log"] is True
    assert logs["truncated"] is False
    assert logs["next_continuation_token"] is None
    assert logs["log_available"] is False
    assert logs["user_message"] == "Airflow returned no remote log content for this task attempt."
