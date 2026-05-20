from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import confluence_connectors as confluence_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import openmetadata_connectors as openmetadata_connector_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_confluence_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_confluence_connector_upsert_list_update_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/v1/admin/confluence-connectors",
        json={
            "name": "confluence-main",
            "workspace_id": "default",
            "connection": {
                "provider": "confluence",
                "base_url": "https://confluence.example.com",
                "username": "reader@example.com",
                "api_token": "secret-token",
                "auth_type": "bearer",
                "verify_tls": True,
                "space_key": "DOCS",
                "page_limit": 12,
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["name"] == "confluence-main"
    assert payload["has_api_token"] is True
    assert payload["connection"]["api_token"] is None
    connector_id = payload["id"]

    list_response = client.get(
        "/v1/admin/confluence-connectors",
        params={"workspace_id": "default"},
    )
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/confluence-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "confluence-main-updated",
            "workspace_id": "default",
            "connection": {
                "provider": "confluence",
                "base_url": "https://confluence.example.com/wiki",
                "username": "reader@example.com",
                "api_token": None,
                "auth_type": "basic",
                "verify_tls": False,
                "space_key": "ANL",
                "page_limit": 7,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "confluence-main-updated"
    assert updated["connection"]["auth_type"] == "basic"
    assert updated["connection"]["verify_tls"] is False
    assert updated["has_api_token"] is True

    delete_response = client.delete(
        f"/v1/admin/confluence-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
