from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.auth import _user_repository, seed_local_admin_account
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import agents as agent_routes
from rhetorica_api.app.routes import chat_stream as chat_stream_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.security.passwords import hash_password

ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "adminadmin"
MEMBER_PASSWORD = "member-pass-123"


def _client(tmp_path: Path, *, auth_enabled: bool = False) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_agents.db"
    settings.auth_enabled = auth_enabled
    settings.auth_default_workspace_id = "default"
    settings.auth_jwt_secret = "test-jwt-secret-0000000000000000"
    settings.auth_local_admin_login = ADMIN_LOGIN if auth_enabled else ""
    settings.auth_local_admin_password = ADMIN_PASSWORD if auth_enabled else ""
    settings.auth_local_admin_display_name = "Admin User"

    execution_routes._runtime.cache_clear()
    agent_routes._repository.cache_clear()
    chat_stream_routes._get_factory.cache_clear()
    _user_repository.cache_clear()

    _, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()
    if auth_enabled:
        seed_local_admin_account()
    return TestClient(create_app())


def _login(client: TestClient, *, login: str = ADMIN_LOGIN, password: str = ADMIN_PASSWORD) -> str:
    response = client.post("/v1/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _agent_payload(slug: str, title: str, *, is_active: bool = True) -> dict:
    return {
        "workspace_id": "default",
        "slug": slug,
        "title": title,
        "description": f"{title} agent",
        "admin_prompt": "Stay inside governed analytics.",
        "is_active": is_active,
        "llm_connector_name": None,
        "allowed_database_connector_names": ["primary-db"],
        "allowed_rag_connector_names": [],
        "allowed_mcp_connector_names": ["mcp-jira-omni"],
        "allowed_workspace_skill_ids": [],
        "settings": {
            "run_sql_allowed": True,
            "temperature": 0.2,
            "max_tokens": 1024,
        },
    }


def test_agent_public_list_reactions_and_sorting(tmp_path: Path) -> None:
    client = _client(tmp_path)

    alpha = client.post("/v1/admin/agents", json=_agent_payload("alpha", "Alpha")).json()
    beta = client.post("/v1/admin/agents", json=_agent_payload("beta", "Beta")).json()
    hidden = client.post(
        "/v1/admin/agents",
        json=_agent_payload("hidden", "Hidden", is_active=False),
    ).json()

    assert alpha["id"] != beta["id"] != hidden["id"]

    public_list = client.get("/v1/agents", params={"workspace_id": "default"})
    assert public_list.status_code == 200
    assert [agent["slug"] for agent in public_list.json()] == ["alpha", "beta"]
    assert "admin_prompt" not in public_list.json()[0]
    assert public_list.json()[0]["allowed_workspace_skill_ids"] == []
    assert public_list.json()[0]["allowed_mcp_connector_names"] == ["mcp-jira-omni"]

    first_star = client.post(f"/v1/agents/{beta['id']}/star", params={"workspace_id": "default"})
    second_star = client.post(f"/v1/agents/{beta['id']}/star", params={"workspace_id": "default"})
    favorite = client.post(
        f"/v1/agents/{beta['id']}/favorite",
        params={"workspace_id": "default"},
    )

    assert first_star.status_code == 200
    assert second_star.status_code == 200
    assert second_star.json()["star_count"] == 1
    assert favorite.status_code == 200
    assert favorite.json()["favorite"] is True

    sorted_list = client.get("/v1/agents", params={"workspace_id": "default"}).json()
    assert [agent["slug"] for agent in sorted_list] == ["beta", "alpha"]
    assert sorted_list[0]["star_count"] == 1
    assert sorted_list[0]["is_starred"] is True
    assert sorted_list[0]["is_favorite"] is True

    admin_list = client.get("/v1/admin/agents", params={"workspace_id": "default"}).json()
    assert {agent["slug"] for agent in admin_list} == {"alpha", "beta", "hidden"}
    assert next(agent for agent in admin_list if agent["slug"] == "alpha")["admin_prompt"]

    unstar = client.delete(f"/v1/agents/{beta['id']}/star", params={"workspace_id": "default"})
    assert unstar.status_code == 200
    assert unstar.json()["star_count"] == 0


def test_admin_create_rejects_duplicate_slug_without_mutating_existing_agent(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    created = client.post("/v1/admin/agents", json=_agent_payload("alpha", "Alpha"))
    assert created.status_code == 200

    duplicate_payload = _agent_payload("alpha", "Mutated")
    duplicate = client.post("/v1/admin/agents", json=duplicate_payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "agent_slug_conflict"
    admin_list = client.get("/v1/admin/agents", params={"workspace_id": "default"}).json()
    assert [agent["title"] for agent in admin_list if agent["slug"] == "alpha"] == ["Alpha"]


def test_analyst_can_read_and_star_but_not_admin_mutate_agents(tmp_path: Path) -> None:
    client = _client(tmp_path, auth_enabled=True)
    admin_token = _login(client)
    user_repository = UserAccountRepository(db=build_execution_service(settings.database_url)[1])
    user_repository.create_local_user(
        workspace_id="default",
        login="analyst",
        password_hash=hash_password(MEMBER_PASSWORD),
        role="analyst",
        display_name="Analyst User",
    )
    analyst_token = _login(client, login="analyst", password=MEMBER_PASSWORD)

    create_response = client.post(
        "/v1/admin/agents",
        json=_agent_payload("shared", "Shared"),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 200
    agent_id = create_response.json()["id"]

    list_response = client.get(
        "/v1/agents",
        params={"workspace_id": "default"},
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert list_response.status_code == 200
    assert list_response.json()[0]["slug"] == "shared"

    star_response = client.post(
        f"/v1/agents/{agent_id}/star",
        params={"workspace_id": "default"},
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert star_response.status_code == 200
    assert star_response.json()["star_count"] == 1

    blocked_response = client.post(
        "/v1/admin/agents",
        json=_agent_payload("blocked", "Blocked"),
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert blocked_response.status_code == 403
    assert blocked_response.json()["detail"] == "admin_access_denied"


def test_chat_conversation_pins_agent_and_rejects_switch(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first_agent = client.post("/v1/admin/agents", json=_agent_payload("first", "First")).json()
    second_agent = client.post("/v1/admin/agents", json=_agent_payload("second", "Second")).json()

    create_response = client.post(
        "/v1/chat/conversation",
        json={
            "workspace_id": "default",
            "title": "Pinned agent chat",
            "agent_id": first_agent["id"],
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["agent_id"] == first_agent["id"]

    missing_agent_response = client.post(
        "/v1/chat/conversation",
        json={
            "workspace_id": "default",
            "title": "Missing agent chat",
            "agent_id": "missing-agent",
        },
    )
    assert missing_agent_response.status_code == 404
    assert missing_agent_response.json()["detail"] == "agent_not_found"

    switch_response = client.post(
        "/v1/chat/ask/stream",
        json={
            "workspace_id": "default",
            "question": "Use another agent",
            "conversation_id": create_response.json()["id"],
            "agent_id": second_agent["id"],
        },
    )
    assert switch_response.status_code == 409
    assert switch_response.json()["detail"] == "conversation_agent_locked"
