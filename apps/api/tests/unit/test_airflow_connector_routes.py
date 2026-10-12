from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import airflow_connectors as airflow_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import gitlab_connectors as gitlab_connector_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_airflow_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    gitlab_connector_routes._repository.cache_clear()
    airflow_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_airflow_connector_upsert_list_update_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/v1/admin/airflow-connectors",
        json={
            "name": "airflow-main",
            "workspace_id": "default",
            "connection": {
                "provider": "airflow",
                "base_url": "https://airflow.example.com/",
                "username": "airflow-user",
                "password": "secret-password",
                "use_when": "Use for read-only Airflow DAG inspection.",
                "auth_type": "basic",
                "verify_tls": True,
                "api_path": "/api/v1",
                "dag_limit": 8,
                "dag_run_limit": 7,
                "task_limit": 6,
                "log_max_chars": 5000,
                "timeout_seconds": 5,
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["name"] == "airflow-main"
    assert payload["connection"]["password"] is None
    assert payload["connection"]["base_url"] == "https://airflow.example.com"
    assert payload["connection"]["use_when"] == "Use for read-only Airflow DAG inspection."
    assert payload["has_password"] is True
    connector_id = payload["id"]

    list_response = client.get(
        "/v1/admin/airflow-connectors",
        params={"workspace_id": "default"},
    )
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/airflow-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "airflow-renamed",
            "workspace_id": "default",
            "connection": {
                "provider": "airflow",
                "base_url": "https://airflow.example.com/custom/",
                "username": "airflow-user",
                "password": None,
                "use_when": "Use for DAG runs and task logs.",
                "auth_type": "basic",
                "verify_tls": False,
                "api_path": "api/v1/",
                "dag_limit": 9,
                "dag_run_limit": 8,
                "task_limit": 7,
                "log_max_chars": 6000,
                "timeout_seconds": 6,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "airflow-renamed"
    assert updated["connection"]["api_path"] == "/api/v1"
    assert updated["connection"]["password"] is None
    assert updated["connection"]["verify_tls"] is False
    assert updated["has_password"] is True

    delete_response = client.delete(
        f"/v1/admin/airflow-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
