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
    settings.database_url = f"sqlite:///{tmp_path}/api_openmetadata_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_openmetadata_connector_upsert_list_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)
    create_response = client.post(
        "/v1/admin/openmetadata-connectors",
        json={
            "name": "om-main",
            "workspace_id": "default",
            "connection": {
                "provider": "openmetadata",
                "base_url": "http://openmetadata.local:8585",
                "api_token": "token-1",
                "verify_tls": False,
                "service_name": "clickhouse_prod",
                "database_name": "analytics",
                "schema_name": "dm",
                "mcp_tables_url": "http://127.0.0.1:8088/tables",
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["name"] == "om-main"
    assert payload["has_api_token"] is True
    assert payload["connection"]["api_token"] is None
    connector_id = payload["id"]

    list_response = client.get(
        "/v1/admin/openmetadata-connectors", params={"workspace_id": "default"}
    )
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/openmetadata-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "om-main-updated",
            "workspace_id": "default",
            "connection": {
                "provider": "openmetadata",
                "base_url": "http://openmetadata.local:8585",
                "api_token": None,
                "verify_tls": True,
                "service_name": "clickhouse_prod",
                "database_name": "analytics",
                "schema_name": "dm",
                "mcp_tables_url": None,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "om-main-updated"
    assert updated["has_api_token"] is True
    assert updated["connection"]["mcp_tables_url"] is None

    delete_response = client.delete(
        f"/v1/admin/openmetadata-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_openmetadata_connector_normalizes_mcp_url(tmp_path: Path) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/v1/admin/openmetadata-connectors",
        json={
            "name": "om-mcp",
            "workspace_id": "default",
            "connection": {
                "provider": "openmetadata",
                "base_url": "https://openmetadata.local:8585",
                "api_token": "token-1",
                "verify_tls": True,
                "mcp_tables_url": "https://openmetadata.local:8585/mcp/sse/",
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["connection"]["mcp_tables_url"] == "https://openmetadata.local:8585/mcp"
    connector_id = payload["id"]

    update_response = client.put(
        f"/v1/admin/openmetadata-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "om-mcp",
            "workspace_id": "default",
            "connection": {
                "provider": "openmetadata",
                "base_url": "https://openmetadata.local:8585",
                "api_token": None,
                "verify_tls": True,
                "mcp_tables_url": "https://openmetadata.local:8585",
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["connection"]["mcp_tables_url"] == "https://openmetadata.local:8585/mcp"
