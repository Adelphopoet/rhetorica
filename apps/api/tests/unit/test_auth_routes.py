from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from rhetorica_api.app import auth as auth_module
from rhetorica_api.app import rate_limits as rate_limit_module
from rhetorica_api.app.auth import (
    _api_token_repository,
    _user_repository,
    seed_local_admin_account,
    validate_auth_token_peppers,
)
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import users as user_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.domain.rate_limits import RateLimitDecision, RateLimitUnavailable
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.security.passwords import hash_password

ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "adminadmin"
MEMBER_PASSWORD = "member-pass-123"


class FakeApiRateLimitService:
    def __init__(self) -> None:
        self.counts: dict[tuple[str, str], int] = {}
        self.reset_calls: list[tuple[str, str]] = []
        self.unavailable = False

    async def check(
        self,
        *,
        workspace_id: str,
        user_id: str,
        requests_per_hour: int | None,
    ) -> RateLimitDecision | None:
        if requests_per_hour is None:
            return None
        if self.unavailable:
            raise RateLimitUnavailable("rate_limit_unavailable")
        key = (workspace_id, user_id)
        count = self.counts.get(key, 0)
        if count >= requests_per_hour:
            return RateLimitDecision(False, requests_per_hour, 0, 120)
        count += 1
        self.counts[key] = count
        return RateLimitDecision(True, requests_per_hour, requests_per_hour - count, 3600)

    async def reset(self, *, workspace_id: str, user_id: str) -> None:
        if self.unavailable:
            raise RateLimitUnavailable("rate_limit_unavailable")
        self.counts.pop((workspace_id, user_id), None)
        self.reset_calls.append((workspace_id, user_id))


def _client(
    tmp_path: Path,
    *,
    bootstrap_enabled: bool = False,
) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_auth.db"
    settings.auth_enabled = True
    settings.auth_default_workspace_id = "default"
    settings.auth_bootstrap_enabled = bootstrap_enabled
    settings.auth_bootstrap_login_secret = "test-bootstrap-secret-000000000000"
    settings.auth_jwt_secret = "test-jwt-secret-0000000000000000"
    settings.auth_local_admin_login = ADMIN_LOGIN
    settings.auth_local_admin_password = ADMIN_PASSWORD
    settings.auth_local_admin_display_name = "Admin User"
    settings.auth_keycloak_enabled = True
    settings.auth_keycloak_issuer_url = "https://auth.example.com/realms/rhetorica"
    settings.auth_keycloak_client_id = "rhetorica-dev"
    settings.auth_keycloak_admin_groups = "kc-rhetorica-admin"
    settings.auth_keycloak_analyst_groups = ""
    settings.auth_keycloak_require_mapped_group = False

    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    _user_repository.cache_clear()
    _api_token_repository.cache_clear()

    _, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()
    seed_local_admin_account()
    return TestClient(create_app())


def _login(client: TestClient, *, login: str = ADMIN_LOGIN, password: str | None = None) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"login": login, "password": password or ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_login_succeeds_with_valid_admin_credentials(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/auth/login",
        json={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["workspace_id"] == "default"
    assert payload["role"] == "admin"
    assert payload["user"]["login"] == "admin"
    assert "access_token" in payload
    assert settings.auth_cookie_name in response.cookies


def test_login_fails_with_wrong_password(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/auth/login",
        json={"login": ADMIN_LOGIN, "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_credentials"


def test_protected_route_rejects_anonymous_request(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/v1/auth/session")

    assert response.status_code == 401
    assert response.json()["detail"] == "not_authenticated"


def test_protected_route_accepts_jwt_from_local_account(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    response = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["login"] == "admin"
    assert response.json()["user"]["auth_provider"] == "local"


def test_user_can_issue_list_use_and_revoke_personal_access_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    session_token = _login(client)
    headers = {"Authorization": f"Bearer {session_token}"}

    created = client.post(
        "/v1/auth/tokens",
        json={
            "name": "Data notebook",
            "scopes": ["api:read", "mcp:read", "mcp:execute"],
            "lifetime_days": 90,
            "auto_renew": True,
        },
        headers=headers,
    )

    assert created.status_code == 201
    payload = created.json()
    token = payload["token"]
    assert token.startswith("rh_pat_")
    assert payload["lifetime_days"] == 90
    assert payload["auto_renew"] is True
    assert created.headers["cache-control"] == "no-store"
    assert created.headers["pragma"] == "no-cache"
    listed = client.get("/v1/auth/tokens", headers=headers)
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json()[0]["name"] == "Data notebook"
    assert "token" not in listed.json()[0]

    session = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert session.status_code == 200
    assert session.json()["user"]["login"] == ADMIN_LOGIN

    revoked = client.delete(f"/v1/auth/tokens/{payload['id']}", headers=headers)
    assert revoked.status_code == 204
    rejected = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "invalid_or_expired_personal_access_token"


def test_personal_access_token_scope_blocks_write_request(tmp_path: Path) -> None:
    client = _client(tmp_path)
    session_token = _login(client)
    created = client.post(
        "/v1/auth/tokens",
        json={"name": "Read only", "scopes": ["api:read"], "lifetime_days": 90},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    token = created.json()["token"]

    response = client.post(
        "/v1/admin/users",
        json={"login": "blocked", "password": "strong-pass-123"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient_token_scope"


def test_token_digest_peppers_must_be_present_and_distinct(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_jwt_secret", "jwt-secret")
    monkeypatch.setattr(settings, "auth_api_token_pepper", "pat-pepper")
    monkeypatch.setattr(settings, "auth_oauth_token_pepper", "oauth-pepper")
    validate_auth_token_peppers()

    monkeypatch.setattr(settings, "auth_oauth_token_pepper", "jwt-secret")
    with pytest.raises(RuntimeError, match="auth_token_secrets_must_be_distinct"):
        validate_auth_token_peppers()

    monkeypatch.setattr(settings, "auth_oauth_token_pepper", "")
    with pytest.raises(RuntimeError, match="AUTH_OAUTH_TOKEN_PEPPER_required"):
        validate_auth_token_peppers()


def test_production_mcp_cannot_disable_auth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "auth_enabled", False)

    with pytest.raises(RuntimeError, match="MCP_requires_AUTH_ENABLED"):
        validate_auth_token_peppers()


def test_mcp_rejects_browser_session_credential(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session_token = _login(client)
        response = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {session_token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "mcp_integration_credential_required"
    assert "resource_metadata=" in response.headers["www-authenticate"]


def test_mcp_scopes_separate_discovery_from_execution(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session_token = _login(client)
        created = client.post(
            "/v1/auth/tokens",
            json={"name": "MCP discovery", "scopes": ["mcp:read"], "lifetime_days": 90},
            headers={"Authorization": f"Bearer {session_token}"},
        )
        token = created.json()["token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        initialized = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            },
        )
        assert initialized.status_code == 200
        tools = client.post(
            "/mcp",
            headers={**headers, "MCP-Protocol-Version": "2025-06-18"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert tools.status_code == 200

        agents = client.post(
            "/mcp",
            headers={**headers, "MCP-Protocol-Version": "2025-06-18"},
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {"name": "list_agents", "arguments": {}},
            },
        )
        assert agents.status_code == 200
        assert agents.json()["result"]["structuredContent"]["agents"] == []

        denied = client.post(
            "/mcp",
            headers={**headers, "MCP-Protocol-Version": "2025-06-18"},
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "run_sql", "arguments": {"query": "SELECT 1"}},
            },
        )
        assert denied.status_code == 403
        assert denied.json()["detail"] == "insufficient_token_scope"


def test_execute_only_pat_is_normalized_for_mcp_lifecycle(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session_token = _login(client)
        created = client.post(
            "/v1/auth/tokens",
            json={
                "name": "MCP execution",
                "scopes": ["mcp:execute"],
                "lifetime_days": 90,
            },
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert created.status_code == 201
        assert created.json()["scopes"] == ["mcp:read", "mcp:execute"]
        token = created.json()["token"]

        initialized = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            },
        )

    assert initialized.status_code == 200


def test_mcp_read_scope_cannot_bypass_execution_with_json_rpc_batch(tmp_path: Path) -> None:
    client = _client(tmp_path)
    session_token = _login(client)
    created = client.post(
        "/v1/auth/tokens",
        json={"name": "MCP discovery", "scopes": ["mcp:read"], "lifetime_days": 90},
        headers={"Authorization": f"Bearer {session_token}"},
    )

    response = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {created.json()['token']}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "run_sql", "arguments": {"query": "SELECT 1"}},
            },
        ],
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient_token_scope"


def test_admin_can_create_local_user(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    response = client.post(
        "/v1/admin/users",
        json={"login": "new-user", "password": "strong-pass-123", "display_name": "New User"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["login"] == "new-user"
    assert payload["workspace_id"] == "default"
    assert payload["role"] == "admin"
    assert payload["auth_provider"] == "local"


def test_admin_can_delete_local_user(tmp_path: Path, monkeypatch) -> None:
    fake_service = FakeApiRateLimitService()
    monkeypatch.setattr(user_routes, "api_rate_limit_service", lambda: fake_service)
    client = _client(tmp_path)
    access_token = _login(client)

    create_response = client.post(
        "/v1/admin/users",
        json={
            "login": "delete-me",
            "password": "strong-pass-123",
            "display_name": "Delete Me",
            "role": "admin",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert create_response.status_code == 200
    user_id = create_response.json()["id"]
    enabled = client.put(
        f"/v1/admin/users/{user_id}/rate-limit",
        json={"requests_per_hour": 20},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert enabled.status_code == 200

    response = client.delete(
        f"/v1/admin/users/{user_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 204
    assert fake_service.reset_calls == [("default", user_id)]

    list_response = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert [user["login"] for user in list_response.json()] == ["admin"]


def test_admin_can_list_workspace_users(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    create_response = client.post(
        "/v1/admin/users",
        json={"login": "listed-user", "password": "strong-pass-123", "display_name": "Listed User"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert create_response.status_code == 200

    response = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [user["login"] for user in payload] == ["admin", "listed-user"]
    assert all(user["api_rate_limit_per_hour"] is None for user in payload)


def test_admin_can_enable_reset_and_disable_user_api_rate_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_service = FakeApiRateLimitService()
    monkeypatch.setattr(user_routes, "api_rate_limit_service", lambda: fake_service)
    client = _client(tmp_path)
    access_token = _login(client)
    created = client.post(
        "/v1/admin/users",
        json={"login": "limited", "password": MEMBER_PASSWORD, "role": "analyst"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    user_id = created.json()["id"]

    enabled = client.put(
        f"/v1/admin/users/{user_id}/rate-limit",
        json={"requests_per_hour": 20},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["api_rate_limit_per_hour"] == 20

    fake_service.counts[("default", user_id)] = 7
    reset = client.post(
        f"/v1/admin/users/{user_id}/rate-limit/reset",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert reset.status_code == 200
    assert reset.json()["api_rate_limit_per_hour"] == 20
    assert fake_service.counts == {}

    disabled = client.delete(
        f"/v1/admin/users/{user_id}/rate-limit",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["api_rate_limit_per_hour"] is None
    assert fake_service.reset_calls == [("default", user_id), ("default", user_id)]


@pytest.mark.parametrize("value", [0, -1, 20.5, "20"])
def test_user_api_rate_limit_rejects_invalid_values(tmp_path: Path, value) -> None:
    client = _client(tmp_path)
    access_token = _login(client)
    user_id = client.post(
        "/v1/admin/users",
        json={"login": "limited", "password": MEMBER_PASSWORD},
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()["id"]

    response = client.put(
        f"/v1/admin/users/{user_id}/rate-limit",
        json={"requests_per_hour": value},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 422
    listed = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert next(user for user in listed.json() if user["id"] == user_id)[
        "api_rate_limit_per_hour"
    ] is None


def test_non_admin_cannot_manage_user_api_rate_limit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    repository = UserAccountRepository(db=build_execution_service(settings.database_url)[1])
    analyst = repository.create_local_user(
        workspace_id="default",
        login="analyst",
        password_hash=hash_password(MEMBER_PASSWORD),
        role="analyst",
        display_name="Analyst User",
    )
    access_token = _login(client, login="analyst", password=MEMBER_PASSWORD)

    response = client.put(
        f"/v1/admin/users/{analyst.id}/rate-limit",
        json={"requests_per_hour": 20},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_access_denied"


def test_user_rate_limit_is_shared_by_session_and_pat_and_blocks_handler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_service = FakeApiRateLimitService()
    monkeypatch.setattr(rate_limit_module, "api_rate_limit_service", lambda: fake_service)
    client = _client(tmp_path)
    admin_token = _login(client)
    created = client.post(
        "/v1/admin/users",
        json={"login": "limited", "password": MEMBER_PASSWORD, "role": "analyst"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_id = created.json()["id"]
    session_token = _login(client, login="limited", password=MEMBER_PASSWORD)
    pat_response = client.post(
        "/v1/auth/tokens",
        json={"name": "API", "scopes": ["api:read"], "lifetime_days": 90},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    pat = pat_response.json()["token"]
    client.put(
        f"/v1/admin/users/{user_id}/rate-limit",
        json={"requests_per_hour": 2},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    session = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {session_token}"},
    )
    token = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {pat}"},
    )
    blocked = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {session_token}"},
    )

    assert session.status_code == 200
    assert session.headers["ratelimit-remaining"] == "1"
    assert token.status_code == 200
    assert token.headers["ratelimit-remaining"] == "0"
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "api_rate_limit_exceeded"
    assert blocked.headers["retry-after"] == "120"
    assert blocked.headers["ratelimit-limit"] == "2"


def test_limited_admin_can_reset_and_disable_own_exhausted_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_service = FakeApiRateLimitService()
    monkeypatch.setattr(rate_limit_module, "api_rate_limit_service", lambda: fake_service)
    monkeypatch.setattr(user_routes, "api_rate_limit_service", lambda: fake_service)
    client = _client(tmp_path)
    admin_token = _login(client)
    headers = {"Authorization": f"Bearer {admin_token}"}
    admin_id = client.get("/v1/admin/users", headers=headers).json()[0]["id"]
    client.put(
        f"/v1/admin/users/{admin_id}/rate-limit",
        json={"requests_per_hour": 1},
        headers=headers,
    )

    assert client.get("/v1/auth/session", headers=headers).status_code == 200
    assert client.get("/v1/auth/session", headers=headers).status_code == 429

    reset = client.post(
        f"/v1/admin/users/{admin_id}/rate-limit/reset",
        headers=headers,
    )
    assert reset.status_code == 200
    assert client.get("/v1/auth/session", headers=headers).status_code == 200

    disabled = client.delete(
        f"/v1/admin/users/{admin_id}/rate-limit",
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["api_rate_limit_per_hour"] is None
    assert client.get("/v1/auth/session", headers=headers).status_code == 200


def test_limited_admin_can_disable_own_limit_when_redis_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_service = FakeApiRateLimitService()
    monkeypatch.setattr(rate_limit_module, "api_rate_limit_service", lambda: fake_service)
    monkeypatch.setattr(user_routes, "api_rate_limit_service", lambda: fake_service)
    client = _client(tmp_path)
    admin_token = _login(client)
    headers = {"Authorization": f"Bearer {admin_token}"}
    admin_id = client.get("/v1/admin/users", headers=headers).json()[0]["id"]
    client.put(
        f"/v1/admin/users/{admin_id}/rate-limit",
        json={"requests_per_hour": 1},
        headers=headers,
    )
    fake_service.unavailable = True

    disabled = client.delete(
        f"/v1/admin/users/{admin_id}/rate-limit",
        headers=headers,
    )

    assert disabled.status_code == 200
    assert disabled.json()["api_rate_limit_per_hour"] is None
    assert client.get("/v1/auth/session", headers=headers).status_code == 200


def test_unlimited_user_skips_limiter_and_limited_user_gets_503_when_redis_is_down(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_service = FakeApiRateLimitService()
    fake_service.unavailable = True
    monkeypatch.setattr(rate_limit_module, "api_rate_limit_service", lambda: fake_service)
    client = _client(tmp_path)
    admin_token = _login(client)

    unlimited = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert unlimited.status_code == 200

    admin_id = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()[0]["id"]
    client.put(
        f"/v1/admin/users/{admin_id}/rate-limit",
        json={"requests_per_hour": 20},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    unavailable = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "rate_limit_unavailable"


def test_current_admin_cannot_delete_self(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)
    admin_id = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()[0]["id"]

    response = client.delete(
        f"/v1/admin/users/{admin_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "cannot_delete_current_user"


def test_create_local_user_rejects_unknown_role(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    response = client.post(
        "/v1/admin/users",
        json={"login": "bad-role", "password": "strong-pass-123", "role": "viewer"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported_user_role"


def test_non_admin_cannot_create_local_user(tmp_path: Path) -> None:
    client = _client(tmp_path)
    repository = UserAccountRepository(db=build_execution_service(settings.database_url)[1])
    repository.create_local_user(
        workspace_id="default",
        login="analyst",
        password_hash=hash_password(MEMBER_PASSWORD),
        role="analyst",
        display_name="Analyst User",
    )
    access_token = _login(client, login="analyst", password=MEMBER_PASSWORD)

    response = client.post(
        "/v1/admin/users",
        json={"login": "blocked", "password": "strong-pass-123"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_access_denied"


def test_analyst_cannot_list_admin_users(tmp_path: Path) -> None:
    client = _client(tmp_path)
    repository = UserAccountRepository(db=build_execution_service(settings.database_url)[1])
    repository.create_local_user(
        workspace_id="default",
        login="analyst",
        password_hash=hash_password(MEMBER_PASSWORD),
        role="analyst",
        display_name="Analyst User",
    )
    access_token = _login(client, login="analyst", password=MEMBER_PASSWORD)

    response = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_access_denied"


def test_workspace_mismatch_still_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    response = client.get(
        "/v1/scenarios",
        params={"workspace_id": "other-workspace"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "workspace_access_denied"


def test_bootstrap_route_is_disabled_by_default(tmp_path: Path) -> None:
    client = _client(tmp_path, bootstrap_enabled=False)

    response = client.post(
        "/v1/auth/bootstrap/session",
        headers={"X-Rhetorica-Bootstrap-Secret": "test-bootstrap-secret-000000000000"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "bootstrap_auth_disabled"


def test_bootstrap_route_works_only_when_explicitly_enabled(tmp_path: Path) -> None:
    client = _client(tmp_path, bootstrap_enabled=True)

    response = client.post(
        "/v1/auth/bootstrap/session",
        headers={"X-Rhetorica-Bootstrap-Secret": "test-bootstrap-secret-000000000000"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["auth_provider"] == "bootstrap"


def test_keycloak_session_provisions_admin_from_group(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    monkeypatch.setattr(
        auth_module,
        "decode_keycloak_id_token",
        lambda _token: {
            "sub": "keycloak-sub-1",
            "preferred_username": "Keycloak.Admin",
            "email": "keycloak.admin@example.test",
            "name": "Keycloak Admin",
            "groups": ["kc-rhetorica-admin"],
        },
    )

    response = client.post("/v1/auth/keycloak/session", json={"id_token": "id-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "admin"
    assert payload["user"]["login"] == "keycloak.admin"
    assert payload["user"]["auth_provider"] == "keycloak"
    assert payload["user"]["auth_subject"] == "keycloak-sub-1"
    assert "access_token" in payload


def test_keycloak_session_defaults_logged_in_user_to_analyst(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    monkeypatch.setattr(
        auth_module,
        "decode_keycloak_id_token",
        lambda _token: {
            "sub": "keycloak-sub-2",
            "preferred_username": "unknown.user",
            "groups": ["kc-other"],
        },
    )

    response = client.post("/v1/auth/keycloak/session", json={"id_token": "id-token"})

    assert response.status_code == 200
    assert response.json()["role"] == "analyst"
    assert response.json()["user"]["login"] == "unknown.user"
