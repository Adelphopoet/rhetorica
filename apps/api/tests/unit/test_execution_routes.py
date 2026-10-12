from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.settings import settings


class _FakeAsyncResult:
    def __init__(self, *, ready: bool = False, result: object = None) -> None:
        self._ready = ready
        self.result = result
        self.revoked = False

    def ready(self) -> bool:
        return self._ready

    def revoke(self, terminate: bool = False) -> None:
        _ = terminate
        self.revoked = True


class _FakeCeleryApp:
    def __init__(self) -> None:
        self.sent_tasks: list[dict[str, object]] = []
        self.results: dict[str, _FakeAsyncResult] = {}

    def send_task(
        self,
        name: str,
        kwargs: dict[str, object],
        task_id: str,
    ) -> _FakeAsyncResult:
        self.sent_tasks.append({"name": name, "kwargs": kwargs, "task_id": task_id})
        result = self.results.setdefault(task_id, _FakeAsyncResult())
        return result

    def AsyncResult(self, task_id: str) -> _FakeAsyncResult:
        return self.results.setdefault(task_id, _FakeAsyncResult())


def _client(tmp_path: Path, fake_celery: _FakeCeleryApp) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_exec_history.db"
    execution_routes._runtime.cache_clear()
    clear_celery_cache = getattr(execution_routes._celery_app, "cache_clear", None)
    if callable(clear_celery_cache):
        clear_celery_cache()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    execution_routes._celery_app = lambda: fake_celery  # type: ignore[assignment]
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    app = create_app()
    return TestClient(app)


def _create_clickhouse_connector(client: TestClient, workspace_id: str = "default") -> None:
    """Helper to create a test connector via the admin API."""
    client.post(
        "/v1/admin/connectors",
        json={
            "name": "test-clickhouse",
            "workspace_id": workspace_id,
            "connection": {
                "engine": "clickhouse",
                "host": "localhost",
                "port": 8123,
                "username": "default",
                "database": "default",
            },
        },
    )


def _create_greenplum_connector(client: TestClient, workspace_id: str = "default") -> None:
    """Helper to create a greenplum test connector via the admin API."""
    client.post(
        "/v1/admin/connectors",
        json={
            "name": "test-greenplum",
            "workspace_id": workspace_id,
            "connection": {
                "engine": "greenplum",
                "host": "localhost",
                "port": 6432,
                "username": "default",
                "database": "dwh",
            },
        },
    )


def test_execute_route_blocks_write_sql(tmp_path: Path) -> None:
    client = _client(tmp_path, _FakeCeleryApp())
    _create_clickhouse_connector(client)

    response = client.post(
        "/v1/sql/execute",
        json={
            "sql": "INSERT INTO t SELECT 1",
            "connector_name": "test-clickhouse",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"


def test_submit_execution_enqueues_and_lists_queued_history(tmp_path: Path) -> None:
    fake_celery = _FakeCeleryApp()
    client = _client(tmp_path, fake_celery)
    _create_clickhouse_connector(client, workspace_id="ws-api")

    response = client.post(
        "/v1/sql/executions",
        json={
            "sql": "SELECT 1",
            "workspace_id": "ws-api",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert len(fake_celery.sent_tasks) == 1
    assert fake_celery.sent_tasks[0]["name"] == "sql_execute.run"

    history = client.get("/v1/sql/executions", params={"workspace_id": "ws-api"})
    rows = history.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"
    assert rows[0]["started_at"] is None


def test_execution_detail_returns_history_and_result_payload(tmp_path: Path) -> None:
    fake_celery = _FakeCeleryApp()
    client = _client(tmp_path, fake_celery)
    _create_clickhouse_connector(client, workspace_id="ws-api")

    queued = client.post(
        "/v1/sql/executions",
        json={
            "sql": "INSERT INTO t SELECT 1",
            "workspace_id": "ws-api",
        },
    ).json()

    execution_id = queued["execution_id"]
    fake_celery.results[execution_id] = _FakeAsyncResult(
        ready=True,
        result={
            "execution_id": execution_id,
            "status": "blocked",
            "engine": "clickhouse",
            "normalized_sql": "INSERT INTO t SELECT 1",
            "applied_row_limit": 1000,
            "warnings": [],
            "violations": [
                {
                    "code": "write_statement",
                    "message": "Detected write operation: INSERT",
                    "severity": "error",
                }
            ],
            "preview": {"columns": [], "rows": []},
            "error_message": "SQL blocked by guard policy.",
        },
    )
    execution_routes._services()[0].execute_prepared(
        execution_id,
        execution_routes.ExecuteSqlRequest.model_validate({
            "sql": "INSERT INTO t SELECT 1",
            "connection": {
                "engine": "clickhouse",
                "host": "localhost",
                "port": 8123,
                "username": "default",
                "database": "default",
            },
            "workspace_id": "ws-api",
        }),
    )

    detail = client.get(f"/v1/sql/executions/{execution_id}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["history"]["status"] == "blocked"
    assert payload["result"]["status"] == "blocked"


def test_cancel_unknown_execution_returns_false(tmp_path: Path) -> None:
    client = _client(tmp_path, _FakeCeleryApp())

    cancel = client.post("/v1/sql/executions/unknown-id/cancel")

    assert cancel.status_code == 200
    assert cancel.json()["cancelled"] is False


def test_execute_route_accepts_greenplum_payload(tmp_path: Path) -> None:
    client = _client(tmp_path, _FakeCeleryApp())
    _create_greenplum_connector(client)

    response = client.post(
        "/v1/sql/execute",
        json={
            "sql": "INSERT INTO t SELECT 1",
            "connector_name": "test-greenplum",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["engine"] == "greenplum"
    assert payload["status"] == "blocked"
