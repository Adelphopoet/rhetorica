from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import mattermost_connectors as mattermost_connector_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_mattermost_connectors.db"
    settings.mattermost_listener_enabled = False
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    mattermost_connector_routes._repository.cache_clear()
    mattermost_connector_routes._agent_repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_mattermost_connector_upsert_list_update_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/v1/admin/mattermost-connectors",
        json={
            "name": "tag-main",
            "workspace_id": "default",
            "connection": {
                "provider": "mattermost",
                "base_url": "https://mattermost.example.com",
                "bot_token": "secret-token",
                "bot_username": "rhetorica",
                "verify_tls": True,
                "listen_enabled": True,
                "send_sql_enabled": True,
                "ambient_listen_enabled": True,
                "ambient_channel_ids": ["channel-1"],
                "ambient_decision_prompt": "Join analytics discussions only.",
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["name"] == "tag-main"
    assert payload["agent_id"] is None
    assert payload["has_bot_token"] is True
    assert payload["connection"]["bot_token"] is None
    assert payload["connection"]["send_sql_enabled"] is True
    assert payload["connection"]["ambient_listen_enabled"] is True
    assert payload["connection"]["ambient_channel_ids"] == ["channel-1"]
    assert payload["connection"]["ambient_decision_prompt"] == "Join analytics discussions only."
    connector_id = payload["id"]

    list_response = client.get(
        "/v1/admin/mattermost-connectors",
        params={"workspace_id": "default"},
    )
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/mattermost-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "tag-updated",
            "workspace_id": "default",
            "connection": {
                "provider": "mattermost",
                "base_url": "https://mattermost.example.com/team",
                "bot_token": None,
                "bot_username": "analytics",
                "verify_tls": False,
                "listen_enabled": False,
                "send_sql_enabled": False,
                "ambient_listen_enabled": False,
                "ambient_channel_ids": [],
                "ambient_decision_prompt": None,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "tag-updated"
    assert updated["connection"]["bot_username"] == "analytics"
    assert updated["connection"]["verify_tls"] is False
    assert updated["connection"]["listen_enabled"] is False
    assert updated["connection"]["send_sql_enabled"] is False
    assert updated["connection"]["ambient_listen_enabled"] is False
    assert updated["connection"]["ambient_channel_ids"] == []
    assert updated["connection"]["ambient_decision_prompt"] is None
    assert updated["has_bot_token"] is True

    delete_response = client.delete(
        f"/v1/admin/mattermost-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_mattermost_connector_accepts_long_ambient_decision_prompt(tmp_path: Path) -> None:
    client = _client(tmp_path)
    prompt = "Decide whether the bot should join.\n" + ("x" * 12_000)

    response = client.post(
        "/v1/admin/mattermost-connectors",
        json={
            "name": "tag-main",
            "workspace_id": "default",
            "connection": {
                "provider": "mattermost",
                "base_url": "https://mattermost.example.com",
                "bot_token": "secret-token",
                "bot_username": "rhetorica",
                "verify_tls": True,
                "listen_enabled": True,
                "ambient_listen_enabled": True,
                "ambient_channel_ids": ["channel-1"],
                "ambient_decision_prompt": prompt,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["connection"]["ambient_decision_prompt"] == prompt


def test_mattermost_connector_does_not_send_sql_by_default(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/admin/mattermost-connectors",
        json={
            "name": "tag-main",
            "workspace_id": "default",
            "connection": {
                "provider": "mattermost",
                "base_url": "https://mattermost.example.com",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["connection"]["send_sql_enabled"] is False
