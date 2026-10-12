from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import gitlab_connectors as gitlab_connector_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import mcp_connectors as mcp_connector_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_mcp_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    gitlab_connector_routes._repository.cache_clear()
    mcp_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def _payload(name: str, *, secret_headers: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "workspace_id": "default",
        "is_active": True,
        "visible_to_external_mcp": True,
        "connection": {
            "provider": "jira",
            "transport": "streamable-http",
            "url": "https://mcp.example.com/mcp",
            "verify_tls": False,
            "headers": {"Enabled-Groups": "jira_read,jira_write"},
            "secret_headers": secret_headers,
            "enabled_groups": "jira_read,jira_write",
            "allowed_tool_names": ["search_issues", "create_issue", "comment_issue"],
            "timeout_seconds": 20,
            "tool_name_prefix": "mcp_jira_main",
            "llm_connector_name": "example-llm",
            "usage_description": "Use for Jira MCP tools.",
            "extra_settings": {"owner": "analytics"},
        },
    }


def test_mcp_connector_crud_masks_secret_headers_and_syncs_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/v1/admin/mcp-connectors",
        json=_payload(
            "mcp-jira-main",
            secret_headers=[
                {
                    "name": "Authorization",
                    "value": "secret-token",
                    "mode": "bearer",
                }
            ],
        ),
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["name"] == "mcp-jira-main"
    assert created["visible_to_external_mcp"] is True
    assert created["has_secret_headers"] is True
    assert created["secret_header_names"] == ["Authorization"]
    assert created["connection"]["secret_headers"] is None
    assert created["connection"]["allowed_tool_names"] == [
        "search_issues",
        "create_issue",
        "comment_issue",
    ]
    assert "secret-token" not in create_response.text
    connector_id = created["id"]

    update_response = client.put(
        f"/v1/admin/mcp-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            **_payload("mcp-jira-main-renamed", secret_headers=None),
            "visible_to_external_mcp": False,
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["has_secret_headers"] is True
    assert update_response.json()["visible_to_external_mcp"] is False

    class _FakeSyncService:
        def sync_connector(self, *, connector_id: str, workspace_id: str) -> tuple[int, None]:
            repository = mcp_connector_routes._repository()
            count = repository.replace_tool_catalog(
                connector_id=connector_id,
                workspace_id=workspace_id,
                tools=[
                    {
                        "name": "search_issues",
                        "description": "Search Jira issues.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"jql": {"type": "string"}},
                        },
                    }
                ],
                status="success",
            )
            return count, None

    monkeypatch.setattr(mcp_connector_routes, "_sync_service", lambda: _FakeSyncService())

    sync_response = client.post(
        f"/v1/admin/mcp-connectors/{connector_id}/sync",
        params={"workspace_id": "default"},
    )
    assert sync_response.status_code == 200
    assert sync_response.json()["synced"] is True
    assert sync_response.json()["tool_count"] == 1

    tools_response = client.get(
        f"/v1/admin/mcp-connectors/{connector_id}/tools",
        params={"workspace_id": "default"},
    )
    assert tools_response.status_code == 200
    tools = tools_response.json()
    assert tools[0]["exposed_tool_name"] == "mcp_jira_main__search_issues"

    delete_response = client.delete(
        f"/v1/admin/mcp-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_mcp_connector_duplicate_name_update_returns_conflict(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = client.post("/v1/admin/mcp-connectors", json=_payload("mcp-one"))
    second = client.post("/v1/admin/mcp-connectors", json=_payload("mcp-two"))
    assert first.status_code == 200
    assert second.status_code == 200

    response = client.put(
        f"/v1/admin/mcp-connectors/{second.json()['id']}",
        params={"workspace_id": "default"},
        json=_payload("mcp-one"),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "mcp_connector_name_conflict"


def test_mcp_connector_tools_are_limited_after_connector_filter(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = client.post("/v1/admin/mcp-connectors", json=_payload("aaa-mcp"))
    second = client.post("/v1/admin/mcp-connectors", json=_payload("zzz-mcp"))
    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.json()["id"]
    second_id = second.json()["id"]

    repository = mcp_connector_routes._repository()
    repository.replace_tool_catalog(
        connector_id=first_id,
        workspace_id="default",
        tools=[{"name": "first_tool", "input_schema": {"type": "object"}}],
        status="success",
    )
    repository.replace_tool_catalog(
        connector_id=second_id,
        workspace_id="default",
        tools=[{"name": "second_tool", "input_schema": {"type": "object"}}],
        status="success",
    )

    response = client.get(
        f"/v1/admin/mcp-connectors/{second_id}/tools",
        params={"workspace_id": "default", "limit": 1},
    )

    assert response.status_code == 200
    tools = response.json()
    assert len(tools) == 1
    assert tools[0]["connector_id"] == second_id
    assert tools[0]["remote_tool_name"] == "second_tool"
