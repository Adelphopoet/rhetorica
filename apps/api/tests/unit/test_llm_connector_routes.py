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
    settings.database_url = f"sqlite:///{tmp_path}/api_llm_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_llm_connector_upsert_list_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)
    create_response = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "openrouter-main",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-4.1",
                "api_key": "secret-key",
                "temperature": 0.2,
                "max_tokens": 900,
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["name"] == "openrouter-main"
    assert payload["has_api_key"] is True
    assert payload["connection"]["api_key"] is None
    connector_id = payload["id"]

    list_response = client.get("/v1/admin/llm-connectors", params={"workspace_id": "default"})
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/llm-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "openrouter-main-updated",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-4.1-mini",
                "api_key": None,
                "verify_tls": False,
                "temperature": 0.1,
                "max_tokens": 1200,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "openrouter-main-updated"
    assert updated["has_api_key"] is True
    assert updated["connection"]["verify_tls"] is False

    delete_response = client.delete(
        f"/v1/admin/llm-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
