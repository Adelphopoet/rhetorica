from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import gitlab_connectors as gitlab_connector_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_gitlab_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    gitlab_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_gitlab_connector_upsert_list_update_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/v1/admin/gitlab-connectors",
        json={
            "name": "gitlab-main",
            "workspace_id": "default",
            "connection": {
                "provider": "gitlab",
                "base_url": "https://gitlab.example.com/",
                "api_token": "secret-token",
                "use_when": "Use for read-only source code lookup.",
                "verify_tls": True,
                "default_project_paths": ["group/app"],
                "default_group_paths": ["platform"],
                "search_limit": 8,
                "read_max_tokens": 1400,
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["name"] == "gitlab-main"
    assert payload["connection"]["use_when"] == "Use for read-only source code lookup."
    assert payload["has_api_token"] is True
    assert payload["connection"]["api_token"] is None
    assert payload["connection"]["base_url"] == "https://gitlab.example.com"
    connector_id = payload["id"]

    list_response = client.get(
        "/v1/admin/gitlab-connectors",
        params={"workspace_id": "default"},
    )
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/gitlab-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "gitlab-main-updated",
            "workspace_id": "default",
            "connection": {
                "provider": "gitlab",
                "base_url": "https://gitlab.example.com/gitlab",
                "api_token": None,
                "use_when": "Use for implementation and config lookup.",
                "verify_tls": False,
                "default_project_paths": ["group/app", "group/lib"],
                "default_group_paths": [],
                "search_limit": 3,
                "read_max_tokens": 900,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "gitlab-main-updated"
    assert updated["connection"]["use_when"] == "Use for implementation and config lookup."
    assert updated["connection"]["verify_tls"] is False
    assert updated["connection"]["default_project_paths"] == ["group/app", "group/lib"]
    assert updated["has_api_token"] is True

    delete_response = client.delete(
        f"/v1/admin/gitlab-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
