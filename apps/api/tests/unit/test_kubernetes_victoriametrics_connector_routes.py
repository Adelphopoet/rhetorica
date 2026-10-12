from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import airflow_connectors as airflow_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import gitlab_connectors as gitlab_connector_routes
from rhetorica_api.app.routes import kubernetes_connectors as kubernetes_connector_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import victoriametrics_connectors as victoriametrics_connector_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_runtime_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    gitlab_connector_routes._repository.cache_clear()
    airflow_connector_routes._repository.cache_clear()
    kubernetes_connector_routes._repository.cache_clear()
    victoriametrics_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_kubernetes_connector_upsert_list_update_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/v1/admin/kubernetes-connectors",
        json={
            "name": "k8s-prod",
            "workspace_id": "default",
            "visible_to_default_assistant": False,
            "connection": {
                "provider": "kubernetes",
                "base_url": "https://k8s.example.com/",
                "bearer_token": "secret-token",
                "use_when": "Use for read-only prod Kubernetes diagnostics.",
                "auth_type": "bearer",
                "verify_tls": True,
                "namespace_allowlist": ["prod", "analytics"],
                "default_limit": 15,
                "max_limit": 40,
                "log_max_chars": 7000,
                "timeout_seconds": 9,
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["connection"]["bearer_token"] is None
    assert payload["has_bearer_token"] is True
    assert payload["visible_to_default_assistant"] is False
    connector_id = payload["id"]

    list_response = client.get(
        "/v1/admin/kubernetes-connectors",
        params={"workspace_id": "default"},
    )
    assert list_response.status_code == 200
    assert list_response.json()[0]["connection"]["namespace_allowlist"] == ["prod", "analytics"]

    update_response = client.put(
        f"/v1/admin/kubernetes-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "k8s-prod",
            "workspace_id": "default",
            "visible_to_default_assistant": True,
            "connection": {
                "provider": "kubernetes",
                "base_url": "https://k8s.example.com/api",
                "bearer_token": None,
                "use_when": "Use for prod namespace only.",
                "auth_type": "bearer",
                "verify_tls": False,
                "namespace_allowlist": ["prod"],
                "default_limit": 10,
                "max_limit": 20,
                "log_max_chars": 6000,
                "timeout_seconds": 8,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["has_bearer_token"] is True
    assert updated["visible_to_default_assistant"] is True
    assert updated["connection"]["verify_tls"] is False

    delete_response = client.delete(
        f"/v1/admin/kubernetes-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_victoriametrics_connector_upsert_list_update_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)
    template = {
        "template_id": "pod_restarts",
        "title": "Pod restarts",
        "use_when": "Inspect pod restart count.",
        "query_template": 'kube_pod_container_status_restarts_total{namespace="$namespace"}',
        "mode": "both",
        "allowed_filters": ["namespace"],
        "default_step": "60s",
        "max_range_seconds": 86400,
        "unit": "count",
        "result_limit": 10,
    }

    create_response = client.post(
        "/v1/admin/victoriametrics-connectors",
        json={
            "name": "vm-prod",
            "workspace_id": "default",
            "visible_to_default_assistant": False,
            "connection": {
                "provider": "victoriametrics",
                "base_url": "https://vm.example.com/",
                "bearer_token": "vm-token",
                "use_when": "Use for metric diagnostics.",
                "auth_type": "bearer",
                "verify_tls": True,
                "query_path": "/prometheus/api/v1/query",
                "query_range_path": "/prometheus/api/v1/query_range",
                "templates": [template],
                "timeout_seconds": 9,
                "max_series": 15,
                "max_datapoints": 500,
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["connection"]["bearer_token"] is None
    assert payload["has_bearer_token"] is True
    assert payload["connection"]["templates"][0]["query_template"] == template["query_template"]
    connector_id = payload["id"]

    list_response = client.get(
        "/v1/admin/victoriametrics-connectors",
        params={"workspace_id": "default"},
    )
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/victoriametrics-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "vm-prod",
            "workspace_id": "default",
            "visible_to_default_assistant": True,
            "connection": {
                "provider": "victoriametrics",
                "base_url": "https://vm.example.com",
                "bearer_token": None,
                "use_when": "Use for metric diagnostics with templates.",
                "auth_type": "bearer",
                "verify_tls": False,
                "query_path": "/prometheus/api/v1/query",
                "query_range_path": "/prometheus/api/v1/query_range",
                "templates": [template],
                "timeout_seconds": 8,
                "max_series": 10,
                "max_datapoints": 300,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["has_bearer_token"] is True
    assert updated["visible_to_default_assistant"] is True
    assert updated["connection"]["verify_tls"] is False

    delete_response = client.delete(
        f"/v1/admin/victoriametrics-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
